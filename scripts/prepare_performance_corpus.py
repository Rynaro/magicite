#!/usr/bin/env python3
"""Regenerate frozen public Wikipedia performance inputs; never quality labels."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

from magicite.engram import parser
from magicite.engram.digests import routing_body_digest
from magicite.engram.model_v1 import EngramFrontmatterV1
from magicite.eval.external import recompute_content_identity_sha256, verify_acquired_corpus_manifest
from magicite.eval.manifests import ArtifactRef, CorpusManifest, QueryRecord
from magicite.eval.runner import candidates_for_corpus

REVISION = "b04c8d1ceb2f5cd4588862100d08de323dccfbaa"
EXPECTED = {
    "corpus-manifest.json": "a5c715d3914b8b3c68ccd9bafd0d447a2f925a5179baeede7edc83da5bbe2d85",
    "selected-records.jsonl": "d5c1547a7e82d0a458d56fb79c7a4fe3908b525d2f24f34c5b39e2990205bb38",
    "attribution.jsonl": "3cc6b322471eeda2acd6aab6b029df27a3363cd2a5f956e91b632daf7f04132c",
}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def acquire(url, path, expected=None, size=None):
    with urllib.request.urlopen(url, timeout=120) as response, path.open("xb") as stream:
        final = response.url
        status = response.status
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
    digest = sha(path.read_bytes())
    if (expected is not None and digest != expected) or (size is not None and path.stat().st_size != size):
        raise ValueError("pinned public source digest/length mismatch")
    return {
        "url": url,
        "resolved_url": final,
        "http_status": status,
        "sha256": digest,
        "bytes": path.stat().st_size,
    }


def batches(path):
    with path.open() as stream:
        while rows := [json.loads(line) for _, line in zip(range(256), stream, strict=False)]:
            yield rows


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--decoder-python", type=Path, required=True)
    a = args.parse_args()
    if not a.output.is_absolute() or a.output != a.output.resolve() or a.output.exists():
        raise ValueError("fresh canonical absolute output required")
    output = a.output
    output.mkdir(parents=True)
    receipts = []
    receipts.append(
        acquire(
            "https://huggingface.co/datasets/wikimedia/wikipedia/resolve/"
            + REVISION
            + "/20231101.en/train-00000-of-00041.parquet",
            output / "source.parquet",
            "382e7f6f09e488b24793a7f7cfc659879d5a22da2cf2efec6491665f0c019677",
            420296449,
        )
    )
    receipts.append(
        acquire(
            f"https://huggingface.co/datasets/wikimedia/wikipedia/raw/{REVISION}/README.md",
            output / "dataset-card.md",
            "a0f2055ac612f924721e9981fa2a49a18adec1c67cc8026f6c97d12f17211b21",
        )
    )
    (output / "licenses").mkdir()
    receipts.append(acquire("https://www.gnu.org/licenses/fdl-1.3.txt", output / "licenses/GFDL-1.3.txt"))
    receipts.append(
        acquire(
            "https://raw.githubusercontent.com/creativecommons/cc-legal-tools-data/"
            "a0dac18d1a773b4cdb7d8dd5d9bf4efab7901030/docs/licenses/by-sa/3.0/legalcode.en.html",
            output / "licenses/CC-BY-SA-3.0.html",
            "c33d7ebdbcc5c8db073e69a794b2ac44805f5d7d9edcfb8b5181a71869a9b7d5",
            54093,
        )
    )
    receipts[-1].update(
        canonical_license_url="https://creativecommons.org/licenses/by-sa/3.0/legalcode",
        official_source_commit="a0dac18d1a773b4cdb7d8dd5d9bf4efab7901030",
        source_semantics="Byte-identical immutable official license representation",
    )
    dump(output / "acquisition.json", receipts)
    rows_path = output / "decoded-rows.jsonl"
    decoder = """
import json,sys,importlib.metadata
from pathlib import Path
import pyarrow.parquet as pq
if importlib.metadata.version('pyarrow') != '21.0.0':
 raise ValueError('pinned preparation decoder required')
pf=pq.ParquetFile(sys.argv[1]); out=Path(sys.argv[2])
assert pf.metadata.num_rows==156289 and set(pf.schema_arrow.names)=={'id','title','url','text'}
count=0
with out.open('x') as stream:
 for batch in pf.iter_batches(batch_size=256,columns=['id','title','url','text']):
  for row in batch.to_pylist():
   stream.write(json.dumps(row,ensure_ascii=False)+'\\n');count+=1
   if count==10000:break
  if count==10000:break
assert count==10000
Path(sys.argv[3]).write_text(json.dumps({'rows':pf.metadata.num_rows,'row_groups':pf.metadata.num_row_groups,'schema':str(pf.schema_arrow),'created_by':pf.metadata.created_by,'pyarrow_version':importlib.metadata.version('pyarrow')}))
"""
    subprocess.run(
        [
            str(a.decoder_python),
            "-c",
            decoder,
            str(output / "source.parquet"),
            str(rows_path),
            str(output / "footer.json"),
        ],
        check=True,
    )
    artdir = output / "artifacts"
    rawdir = output / "raw-text"
    artdir.mkdir()
    rawdir.mkdir()
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    selected = []
    reject = []
    ids = set()
    wrapperids = set()
    refs = []
    queries = []
    titles = {}
    skips = []
    index = 0
    rawtotal = bodytotal = wraptotal = 0
    rawout = (output / "selected-records.jsonl").open("w")
    ledger = (output / "rejections.jsonl").open("w")
    attribution = (output / "attribution.jsonl").open("w")
    for batch in batches(rows_path):
        for row in batch:
            physical = index
            index += 1
            reason = None
            for key in ["id", "title", "url", "text"]:
                if not isinstance(row[key], str) or not row[key].strip():
                    reason = "empty or invalid " + key
                    break
            if row["id"] in ids:
                reason = "duplicate source id"
            if reason:
                rejected = {"physical_row": physical, "id": row["id"], "reason": reason}
                ledger.write(json.dumps(rejected, ensure_ascii=False) + "\n")
                reject.append(rejected)
                continue
            docid = row["id"]
            assert docid.isdecimal() and str(int(docid)) == docid and 0 < int(docid) <= 0xFFFFFFFF, (
                "unsupported/colliding stable id"
            )
            eid = f"egr_{int(docid):08x}"
            name = "wikipedia-" + docid
            assert eid not in wrapperids
            wrapperids.add(eid)
            ids.add(docid)
            raw = row["text"].encode("utf-8")
            rawsha = sha(raw)
            rawpath = rawdir / (docid + ".txt")
            rawpath.write_bytes(raw)
            history = row["url"] + ("&" if "?" in row["url"] else "?") + "action=history"
            attribution_text = (
                "\n\n---\n\nAttribution (added wrapper metadata, not original article "
                "text):\nWikipedia contributors; "
                + row["title"]
                + "; "
                + row["url"]
                + "; contributor history "
                + history
                + (
                    ".\nWikimedia Wikipedia snapshot 20231101.en, dataset revision b04c"
                    "8d1ceb2f5cd4588862100d08de323dccfbaa.\nCC-BY-SA-3.0 and GFDL as de"
                    "clared by pinned dataset card; some text CC-only per card.\nFull s"
                    "ource text preserved; format/header/attribution added for DEVELOP"
                    "MENT performance payload only.\nImported/pending; not reviewed exe"
                    "cutable instructions or authentic relevance labels.\n"
                )
            )
            body = "## Procedure\n" + row["text"] + attribution_text
            bodybytes = body.encode()
            fm = EngramFrontmatterV1.model_validate(
                {
                    "name": name,
                    "id": eid,
                    "version": 1,
                    "intent": {
                        "does": "Preserve full Wikipedia text: " + row["title"],
                        "use_when": "Development performance input only; title-to-document proxy queries",
                        "not_when": (
                            "Executing commands, authentic quality evaluation, production rout"
                            "ing or admission"
                        ),
                    },
                    "routing": {
                        "positive": [row["title"]],
                        "negative": [],
                        "body_digest": routing_body_digest(body),
                    },
                    "origin": {
                        "channel": "imported",
                        "verification_status": "pending",
                        "import_source": row["url"],
                        "content_hashes": {"raw_text_sha256": rawsha},
                        "journal": [],
                    },
                    "capabilities": {
                        "requires": [],
                        "produces": [],
                        "alternatives": [],
                        "conflicts_with": [],
                    },
                    "relations": {"requires": [], "before": [], "supersedes": []},
                    "risk": {
                        "filesystem": "none",
                        "subprocess": {"mode": "none", "tools": []},
                        "network": {"mode": "none", "destinations": []},
                        "secrets": "none",
                    },
                }
            )
            stream = io.StringIO()
            yaml.dump(fm.model_dump(mode="json", exclude_none=True), stream)
            header = ("---\n" + stream.getvalue() + "---\n").encode()
            payload = header + bodybytes
            path = artdir / (docid + ".egr.md")
            path.write_bytes(payload)
            artifact, _ = parser.load_artifact_file(path, registry_root=artdir)
            assert (
                artifact.id == eid
                and artifact.name == name
                and artifact.frontmatter.origin.channel == "imported"
                and artifact.frontmatter.origin.verification_status == "pending"
            )
            assert artifact.frontmatter.routing.body_digest == routing_body_digest(body)
            offset = len(header) + len(b"## Procedure\n")
            assert (
                payload[offset : offset + len(raw)] == raw
                and sha(payload[offset : offset + len(raw)]) == rawsha
            )
            rec = {
                "physical_row": physical,
                **row,
                "wrapper_id": eid,
                "wrapper_name": name,
                "raw_text_path": str(rawpath.relative_to(output)),
                "raw_text_sha256": rawsha,
                "raw_text_bytes": len(raw),
                "wrapper_path": str(path.relative_to(output)),
                "wrapper_sha256": sha(payload),
                "wrapper_bytes": len(payload),
                "body_sha256": sha(bodybytes),
                "body_bytes": len(bodybytes),
                "raw_span_offset": offset,
                "raw_span_length": len(raw),
                "history_url": history,
            }
            rawout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            selected.append(rec)
            attribution.write(
                json.dumps(
                    {
                        "source_id": docid,
                        "title": row["title"],
                        "url": row["url"],
                        "history_url": history,
                        "license": "CC-BY-SA-3.0 and GFDL per card; some text CC-only",
                        "contributors": "Wikipedia contributors",
                        "dataset_revision": "b04c8d1ceb2f5cd4588862100d08de323dccfbaa",
                        "raw_sha256": rawsha,
                        "wrapper_changes": (
                            "Full raw text unchanged; native header/procedure heading and sepa"
                            "rate attribution added"
                        ),
                        "historical_revision_limit": (
                            "Article revision ID not present in dataset; history link is contr"
                            "ibutor history reference, not claimed exact revision URI"
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            refs.append(
                ArtifactRef(
                    path=str(path.relative_to(output)),
                    sha256=sha(payload),
                    role="skill",
                    byte_length=len(payload),
                )
            )
            rawtotal += len(raw)
            bodytotal += len(bodybytes)
            wraptotal += len(payload)
            query = row["title"].strip()
            key = " ".join(query.lower().split())
            if len(queries) < 1000 and key in titles:
                skips.append(
                    {
                        "physical_row": physical,
                        "source_id": docid,
                        "title": row["title"],
                        "normalized_key": key,
                        "prior_source_id": titles[key],
                        "reason": "duplicate under existing corpus validator normalization",
                    }
                )
            if len(queries) < 1000 and key not in titles:
                titles[key] = docid
                queries.append(
                    QueryRecord(
                        query_id="wiki-title-" + docid,
                        query_text=query,
                        split="development",
                        relevance={eid: 1.0},
                        group_id=docid,
                        provenance={
                            "label_origin": "author_created",
                            "method": "machine-derived title/document identity performance proxy",
                            "machine_generated": True,
                            "source_id": docid,
                            "physical_row": physical,
                            "source_url": row["url"],
                            "authentic_quality_label": False,
                            "independent_annotation": False,
                            "official_heldout_test_consulted": False,
                            "group_scope": "document identity bookkeeping, not independent sample guarantee",
                        },
                    )
                )
            if len(selected) % 500 == 0:
                rawout.flush()
                attribution.flush()
                print("VALIDATED_WRAPPERS", len(selected), flush=True)
            if len(selected) == 10000:
                break
        if len(selected) == 10000:
            break
    (output / "query-selection-rejections.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in skips)
    )
    rawout.close()
    ledger.close()
    attribution.close()
    assert len(selected) == 10000 and len(queries) == 1000 and len(ids) == 10000
    identity = recompute_content_identity_sha256([q.to_dict() for q in queries])
    manifest = CorpusManifest(
        corpus_id="wikimedia-wikipedia-20231101-en-first10000-development-performance",
        artifacts=tuple(refs),
        queries=tuple(queries),
        label_origin="author_created",
        dataset_revision="b04c8d1ceb2f5cd4588862100d08de323dccfbaa/20231101.en/train-00000-of-00041.parquet",
        license="CC-BY-SA-3.0; GFDL per pinned card, some text CC-only",
        content_identity_sha256=identity,
    )
    dump(output / "corpus-manifest.json", manifest.to_dict())
    verified, errors = verify_acquired_corpus_manifest(output / "corpus-manifest.json")
    assert not errors and verified is not None
    candidates = candidates_for_corpus(verified)
    assert len(candidates) == 1000 and set(candidates) <= wrapperids
    for r in selected:
        raw = (output / r["raw_text_path"]).read_bytes()
        payload = (output / r["wrapper_path"]).read_bytes()
        assert (
            sha(raw) == r["raw_text_sha256"]
            and sha(payload) == r["wrapper_sha256"]
            and payload[r["raw_span_offset"] : r["raw_span_offset"] + r["raw_span_length"]] == raw
        )
        artifact, _ = parser.load_artifact_file(output / r["wrapper_path"], registry_root=artdir)
        assert artifact.id == r["wrapper_id"] and artifact.name == r["wrapper_name"]
    for name, expected in EXPECTED.items():
        if sha((output / name).read_bytes()) != expected:
            raise ValueError("frozen corpus output mismatch: " + name)
    if identity != "b464650c0ca967c2d7bd01b4700b190c089e17b565f3784beacf108186de4c73":
        raise ValueError("frozen query identity mismatch")
    dump(
        output / "preparation-receipt.json",
        {
            "status": "INPUTS_VERIFIED",
            "hashes": EXPECTED,
            "query_identity": identity,
            "authentic_quality_labels": False,
            "embedding_or_admission_executed": False,
        },
    )


if __name__ == "__main__":
    main()
