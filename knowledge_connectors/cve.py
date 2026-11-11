"""Selected CVE List V5 records, limited to the currently published KEV IDs."""
import json
from pathlib import Path
import re

from .common import (ConnectorError, HTTPClient, SyncResult, canonical_cve,
                     fail_run, iso_timestamp, json_object, preserve_raw, source_writer, utc_now)


_CWE = re.compile(r"CWE-\d+", re.I)


def record_url(cve_id):
    cve_id = canonical_cve(cve_id)
    _, year, serial = cve_id.split("-")
    bucket = f"{int(serial) // 1000}xxx"
    return f"https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/{year}/{bucket}/{cve_id}.json"


def _english(items, field="value"):
    if not isinstance(items, list):
        return None
    candidates = [item for item in items if isinstance(item, dict) and str(item.get(field) or "").strip()]
    selected = next((item for item in candidates if str(item.get("lang") or "").lower().startswith("en")), None)
    selected = selected or (candidates[0] if candidates else None)
    return str(selected[field]).strip() if selected else None


def _containers(payload):
    containers = payload.get("containers")
    if not isinstance(containers, dict) or not isinstance(containers.get("cna"), dict):
        raise ConnectorError("CVE List record lacks its CNA container.")
    cna = containers["cna"]
    adp = containers.get("adp") if isinstance(containers.get("adp"), list) else []
    result = [(cna, "CNA:" + str(cna.get("providerMetadata", {}).get("shortName") or "unknown"))]
    result.extend(
        (item, "ADP:" + str(item.get("providerMetadata", {}).get("shortName") or "unknown"))
        for item in adp if isinstance(item, dict)
    )
    return result


def _metric(containers):
    """Choose one display/filter metric; the original snapshot retains every metric."""
    versions = (("cvssV4_0", "4.0"), ("cvssV3_1", "3.1"), ("cvssV3_0", "3.0"))
    for container, origin in containers:  # CNA first, then ADP in source order.
        metrics = container.get("metrics") if isinstance(container.get("metrics"), list) else []
        for key, version in versions:
            for metric in metrics:
                value = metric.get(key) if isinstance(metric, dict) else None
                if not isinstance(value, dict):
                    continue
                try:
                    base = float(value["baseScore"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not 0 <= base <= 10:
                    continue
                vector = str(value.get("vectorString") or "").strip() or None
                return base, version, vector, origin
    return None, None, None, None


def parse_record(content, expected_id):
    payload = json_object(content, expected_id)
    metadata = payload.get("cveMetadata")
    if not isinstance(metadata, dict):
        raise ConnectorError(f"{expected_id} lacks cveMetadata.")
    cve_id = canonical_cve(metadata.get("cveId"))
    if cve_id != expected_id:
        raise ConnectorError(f"CVE List returned {cve_id} for requested {expected_id}.")
    containers = _containers(payload)
    cna, cna_origin = containers[0]
    state = str(metadata.get("state") or "").strip().lower()
    if state not in {"published", "rejected", "withdrawn"}:
        raise ConnectorError(f"{cve_id} has an unsupported state: {state or '<empty>'}.")
    description = _english(cna.get("descriptions"))
    if not description:
        description = _english(cna.get("rejectedReasons"))
    if not description:
        raise ConnectorError(f"{cve_id} lacks an English description or rejection reason.")
    weaknesses = set()
    references = {}
    for container, origin in containers:
        problem_types = container.get("problemTypes") if isinstance(container.get("problemTypes"), list) else []
        for group in problem_types:
            descriptions = group.get("descriptions") if isinstance(group, dict) else []
            for item in descriptions if isinstance(descriptions, list) else []:
                if not isinstance(item, dict):
                    continue
                values = [item.get("cweId"), item.get("description")]
                for value in values:
                    if match := _CWE.search(str(value or "")):
                        weaknesses.add((match.group(0).upper(), origin))
        refs = container.get("references") if isinstance(container.get("references"), list) else []
        for item in refs:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            references.setdefault((url, origin), set()).update(str(tag) for tag in tags)
    cvss_base, cvss_version, cvss_vector, cvss_origin = _metric(containers)
    return {
        "cve_id": cve_id,
        "published_at": iso_timestamp(
            metadata.get("datePublished"), f"{cve_id} datePublished", optional=True,
        ),
        "modified_at": iso_timestamp(
            metadata.get("dateUpdated"), f"{cve_id} dateUpdated", optional=True,
        ),
        "status": state,
        "description": description,
        "cvss_base": cvss_base,
        "cvss_version": cvss_version,
        "cvss_vector": cvss_vector,
        "cvss_origin": cvss_origin,
        "origin": cna_origin,
        "weaknesses": sorted(weaknesses),
        "references": sorted(
            (url, json.dumps(sorted(tags), ensure_ascii=False), origin)
            for (url, origin), tags in references.items()
        ),
    }


class CVEListConnector:
    source_id = "cve-list-v5"
    space_id = "vulnerability-intelligence"
    source_kind = "cve"
    source_name = "CVE List V5"
    source_url = "https://github.com/CVEProject/cvelistV5"
    license_name = "CVE Program Terms of Use"
    license_url = "https://www.cve.org/Legal/TermsOfUse"

    def register(self, store):
        store.upsert_space(
            self.space_id, "Vulnerability intelligence",
            vector_collection=self.space_id, default_ingestion_profile="structured",
        )
        store.upsert_source(
            self.source_id, self.space_id, self.source_kind, self.source_name,
            self.source_url, license_name=self.license_name, license_url=self.license_url,
        )

    @staticmethod
    def selected_ids(store):
        with store.connect() as db:
            return [row[0] for row in db.execute("SELECT cve_id FROM current_kev ORDER BY cve_id")]

    def sync(self, store, raw_root, *, client=None):
        client = client or HTTPClient()
        self.register(store)
        with source_writer(Path(raw_root).resolve().parent, self.source_id):
            store.recover_abandoned(self.source_id)
            return self._sync_locked(store, raw_root, client)

    def _sync_locked(self, store, raw_root, client):
        identifiers = self.selected_ids(store)
        if not identifiers:
            raise ConnectorError("Publish CISA KEV before synchronizing CVE List records.")
        run = store.start_sync(self.source_id)
        try:
            records, manifest = [], []
            raw_root = Path(raw_root).resolve()
            for cve_id in identifiers:
                url = record_url(cve_id)
                downloaded = client.get(url, max_bytes=4 * 1024 * 1024)
                if downloaded.not_modified:
                    raise ConnectorError("CVE List record unexpectedly returned HTTP 304 without an object cache.")
                record = parse_record(downloaded.content, cve_id)
                path, digest = preserve_raw(raw_root, self.source_id, f"{cve_id}.json",
                                            downloaded.content)
                records.append(record)
                manifest.append({
                    "cve_id": cve_id,
                    "sha256": digest,
                    "path": path.relative_to(raw_root).as_posix(),
                    "url": url,
                })
            manifest_content = json.dumps(
                {"format": "cvelist-v5-selection-v1", "records": manifest},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            manifest_path, digest = preserve_raw(
                raw_root, self.source_id, "manifest.json", manifest_content,
            )
            current = store.published_snapshot(self.source_id)
            if current and current["sha256"] == digest:
                store.finish_without_change(run, stats={"reason": "same-sha256"})
                return SyncResult(self.source_id, "unchanged", current["id"], 0, digest)
            updated = max((str(row.get("modified_at") or "") for row in records), default="") or None
            snapshot = store.stage_snapshot(
                run, digest, str(manifest_path), fetched_at=utc_now(),
                source_updated_at=updated,
                metadata={"format": "cvelist-v5-selection-v1", "cve_count": len(records)},
            )
            with store.write_staged(snapshot) as db:
                db.executemany(
                    """INSERT INTO vulnerabilities
                       (snapshot_id,cve_id,published_at,modified_at,status,description,
                        cvss_base,cvss_version,cvss_vector,cvss_origin,origin)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [(
                        snapshot, row["cve_id"], row["published_at"], row["modified_at"],
                        row["status"], row["description"], row["cvss_base"],
                        row["cvss_version"], row["cvss_vector"], row["cvss_origin"],
                        row["origin"],
                    ) for row in records],
                )
                db.executemany(
                    """INSERT INTO vulnerability_weaknesses
                       (snapshot_id,cve_id,cwe_id,origin) VALUES (?,?,?,?)""",
                    [(snapshot, row["cve_id"], cwe, origin) for row in records
                     for cwe, origin in row["weaknesses"]],
                )
                db.executemany(
                    """INSERT INTO vulnerability_references
                       (snapshot_id,cve_id,url,tags_json,origin) VALUES (?,?,?,?,?)""",
                    [(snapshot, row["cve_id"], url, tags, origin) for row in records
                     for url, tags, origin in row["references"]],
                )
            store.publish(run, stats={"records": len(records), "selected_from_kev": len(identifiers)})
            return SyncResult(self.source_id, "published", snapshot, len(records), digest)
        except Exception as error:
            fail_run(store, run, error)
            if isinstance(error, ConnectorError):
                raise
            raise ConnectorError(f"Could not synchronize CVE List V5: {error}.") from error
