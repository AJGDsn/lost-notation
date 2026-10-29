# Architecture

## Pipeline contract

```text
query generator → discovery adapters → candidate queue → fetch/preserve
                                                    ↓
 public catalogue ← search/index ← classify/embed/cluster ← extract/OCR
```

Every stage consumes stable IDs and writes an artefact plus a processing record. It can therefore be rerun independently, with a new extractor/model version alongside—not over—the prior result.

| Stage | Initial implementation | Scaled option | Why |
| --- | --- | --- | --- |
| Discovery | adapter protocol + JSONL importer | Internet Archive CDX, Common Crawl index, GitHub API, repository/OAI-PMH, Usenet/forum adapters | Sources have distinct query syntax, rate limits, and evidence fields. |
| Preservation | filesystem SHA-256 blobs + SQLite manifest | S3/MinIO + PostgreSQL manifest/WARC | Content addressing deduplicates bytes while retaining each retrieval. |
| Extraction | stdlib HTML parser | trafilatura/Apache Tika, PDF text extractor | Derived text stays linked to original bytes and extractor version. |
| OCR | planned worker interface | OCRmyPDF/Tesseract + layout models | OCR is expensive, image-specific, and needs per-page confidence. |
| Search | SQLite FTS5 | PostgreSQL FTS + OpenSearch | FTS5 makes a one-machine research corpus viable immediately. |
| Vectors | planned versioned embedding table | pgvector or Qdrant | Keep vector search separate from canonical metadata. |

## Core records

`candidate` records where and why a URL was found. `retrieval` records every attempted fetch, headers, timestamp, status, and blob hash. `artefact` records a derived output (text/OCR/render) with a producer version. `project` is a curator-reviewed intellectual work that may join several candidates/retrievals. This distinction avoids equating a URL with a notation system.

## Incremental roadmap

1. Add Internet Archive CDX and Common Crawl adapters, source-specific throttling, and WARC capture.
2. Add PDF/image detection, page rendering, OCR workers, and a review queue.
3. Add a schema-guided LLM assessment worker. Store prompt, model, schema, response, confidence, and evidence citations; require human confirmation for project identity and originality claims.
4. Add embeddings and clustering as versioned derived data; expose nearest-neighbour and graph review tools.
5. Introduce PostgreSQL/pgvector and object storage when corpus size or concurrent workers exceeds SQLite's comfortable single-writer model; keep SQLite as a reproducible local mode.
6. Build a public read-only API/site from reviewed `project` records, with provenance, rights/takedown controls, and version history.

## Query evolution

The query generator emits a record containing its terms, language, template, and generator version. New vocabulary is promoted only through a reviewed lexicon file, so search expansion is auditable and can be replayed. First-person templates reduce the bias toward descriptions of well-known systems.
