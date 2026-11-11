"""FIRST EPSS daily bulk CSV connector."""
import csv
import gzip
import io
import re

from .common import ConnectorError, SingleFileConnector, canonical_cve


_SCORE_DATE = re.compile(r"score_date\s*:\s*(\d{4}-\d{2}-\d{2})", re.I)


class EPSSConnector(SingleFileConnector):
    source_id = "first-epss"
    source_kind = "epss"
    source_name = "FIRST Exploit Prediction Scoring System"
    source_url = "https://epss.empiricalsecurity.com/epss_scores-current.csv.gz"
    license_name = "FIRST EPSS data"
    license_url = "https://www.first.org/epss/data.html"
    filename = "epss_scores-current.csv.gz"
    max_bytes = 64 * 1024 * 1024

    def parse(self, content):
        try:
            chunks, total = [], 0
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > 256 * 1024 * 1024:
                        raise ConnectorError("FIRST EPSS decompressed data exceeds the size limit.")
                    chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            raise ConnectorError("FIRST EPSS did not return valid gzip UTF-8 CSV.") from error
        lines = raw.splitlines()
        comments = [line for line in lines if line.startswith("#")]
        data_lines = [line for line in lines if line and not line.startswith("#")]
        if not data_lines:
            raise ConnectorError("FIRST EPSS CSV contains no rows.")
        reader = csv.DictReader(data_lines)
        if not {"cve", "epss", "percentile"} <= set(reader.fieldnames or ()):
            raise ConnectorError("FIRST EPSS CSV has unexpected columns.")
        match = next((match for line in comments if (match := _SCORE_DATE.search(line))), None)
        if match is None:
            raise ConnectorError("FIRST EPSS CSV lacks its score_date metadata.")
        score_date = match.group(1)
        rows = []
        for number, row in enumerate(reader, 2):
            try:
                score, percentile = float(row["epss"]), float(row["percentile"])
            except (TypeError, ValueError) as error:
                raise ConnectorError(f"FIRST EPSS row {number} has invalid scores.") from error
            if not (0 <= score <= 1 and 0 <= percentile <= 1):
                raise ConnectorError(f"FIRST EPSS row {number} has scores outside 0..1.")
            rows.append((canonical_cve(row["cve"]), score, percentile, score_date))
        if not rows:
            raise ConnectorError("FIRST EPSS CSV contains no score records.")
        if len({row[0] for row in rows}) != len(rows):
            raise ConnectorError("FIRST EPSS CSV contains duplicate CVE identifiers.")
        return {"score_date": score_date, "rows": rows}

    def metadata(self, parsed):
        return {"score_date": parsed["score_date"], "format": "first-epss-csv-v1"}

    def insert(self, db, snapshot_id, parsed):
        db.executemany(
            """INSERT INTO epss
               (snapshot_id,cve_id,score,percentile,score_date,origin)
               VALUES (?,?,?,?,?,?)""",
            [(snapshot_id, cve, score, percentile, score_date, "FIRST EPSS")
             for cve, score, percentile, score_date in parsed["rows"]],
        )
        return len(parsed["rows"])
