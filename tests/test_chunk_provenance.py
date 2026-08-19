from worker.normalize import blocks_to_chunks


def test_blocks_to_chunks_adds_byte_range_and_hash():
    text = "Hello world"
    blocks = [
        {
            "text": text,
            "doc_id": "doc1",
            "title": "Doc 1",
            "section_id": None,
            "source_url": None,
            "doc_type": "document",
            "tags": [],
            "metadata": {},
        }
    ]
    corpus = {
        "corpus_id": "ns_test",
        "chunking": {"target_chars": 200, "overlap_chars": 0, "strategy": "llm"},
    }

    def mock_chat(system: str, user: str) -> str:
        return '{"chunks":[{"start": 0, "end": 11}]}'

    chunks = blocks_to_chunks(blocks, corpus, version_date=None, chat_fn=mock_chat)
    assert len(chunks) == 1
    meta = chunks[0].metadata
    assert "chunk_hash" in meta
    assert "byte_range" in meta
    assert meta["byte_range"]["start"] == 0
    assert meta["byte_range"]["end"] == len(text)
