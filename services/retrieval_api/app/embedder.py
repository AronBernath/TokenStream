import httpx
from typing import List


class TEIEmbedder:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def embed(self, texts: List[str]) -> List[List[float]]:
        # TEI endpoint: POST /embed { "inputs": [...] }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/embed", json={"inputs": texts})
            r.raise_for_status()
            data = r.json()
            # data is list of embeddings
            return data
