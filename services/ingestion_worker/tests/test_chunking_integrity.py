from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[2]
COMMON_ROOT = SERVICES_ROOT / "common"

if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from common.chunking import chunk_text  # noqa: E402


def test_llm_offset_chunking_preserves_text_gaps():
    text = "alpha beta gamma delta"

    def chat_fn(_system: str, _user: str) -> str:
        return '{"chunks":[{"start":0,"end":5},{"start":11,"end":16}]}'

    chunks = chunk_text(text, target_chars=8, chat_fn=chat_fn, use_cache=False)

    assert chunks == ["alpha", "beta", "gamma", "delta"]
