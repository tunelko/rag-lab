"""CISA Known Exploited Vulnerabilities connector."""
from .common import (ConnectorError, SingleFileConnector, canonical_cve,
                     iso_date, json_object, normalized_key)


class KEVConnector(SingleFileConnector):
    source_id = "cisa-kev"
    source_kind = "kev"
    source_name = "CISA Known Exploited Vulnerabilities"
    source_url = "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"
    license_name = "CISA KEV data license"
    license_url = "https://github.com/cisagov/kev-data/blob/develop/LICENSE"
    filename = "known_exploited_vulnerabilities.json"

    def parse(self, content):
        payload = json_object(content, "CISA KEV")
        rows = payload.get("vulnerabilities")
        if not isinstance(rows, list) or not rows:
            raise ConnectorError("CISA KEV contains no vulnerability records.")
        if isinstance(payload.get("count"), int) and payload["count"] != len(rows):
            raise ConnectorError("CISA KEV count does not match its vulnerability records.")
        normalized = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ConnectorError(f"CISA KEV record {index} is not an object.")
            try:
                vendor = str(row["vendorProject"]).strip()
                product = str(row["product"]).strip()
                action = str(row["requiredAction"]).strip()
                date_added = iso_date(row["dateAdded"], f"CISA KEV record {index} dateAdded")
            except KeyError as error:
                raise ConnectorError(f"CISA KEV record {index} lacks {error.args[0]}.") from error
            if not all((vendor, product, action, date_added)):
                raise ConnectorError(f"CISA KEV record {index} has an empty required field.")
            ransomware = str(row.get("knownRansomwareCampaignUse") or "").strip()
            known = (1 if ransomware.casefold() == "known" else
                     0 if ransomware.casefold() == "unknown" else None)
            normalized.append({
                "cve_id": canonical_cve(row.get("cveID")),
                "date_added": date_added,
                "vendor": vendor,
                "vendor_key": normalized_key(vendor),
                "product": product,
                "product_key": normalized_key(product),
                "known_ransomware_use": ransomware or None,
                "ransomware_known": known,
                "required_action": action,
                "due_date": iso_date(
                    row.get("dueDate"), f"CISA KEV record {index} dueDate", optional=True,
                ),
                "notes": str(row.get("notes") or "").strip() or None,
            })
        identifiers = [row["cve_id"] for row in normalized]
        if len(set(identifiers)) != len(identifiers):
            raise ConnectorError("CISA KEV contains duplicate CVE identifiers.")
        return {
            "catalog_version": payload.get("catalogVersion"),
            "date_released": payload.get("dateReleased"),
            "rows": normalized,
        }

    def metadata(self, parsed):
        return {
            "catalog_version": parsed["catalog_version"],
            "date_released": parsed["date_released"],
            "format": "cisa-kev-json",
        }

    def insert(self, db, snapshot_id, parsed):
        db.executemany(
            """INSERT INTO kev
               (snapshot_id,cve_id,date_added,vendor,vendor_key,product,product_key,
                known_ransomware_use,ransomware_known,required_action,due_date,notes,origin)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(
                snapshot_id, row["cve_id"], row["date_added"], row["vendor"],
                row["vendor_key"], row["product"], row["product_key"],
                row["known_ransomware_use"], row["ransomware_known"],
                row["required_action"], row["due_date"], row["notes"], "CISA KEV",
            ) for row in parsed["rows"]],
        )
        return len(parsed["rows"])
