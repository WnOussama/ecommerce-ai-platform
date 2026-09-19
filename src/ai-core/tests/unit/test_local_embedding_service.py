"""
Tests du service d'embedding réel (LocalEmbeddingService) et du choix fait
par la factory. Le vrai modèle ONNX (~80 Mo) n'est pas téléchargé ici : on
remplace la fonction d'embedding de ChromaDB par un faux appelable pour
vérifier le câblage (chargement paresseux unique, batching, dimensions,
absence de repli silencieux).
"""

import pytest

from app.services.rag import factory
from app.services.rag.embedding_service import EmbeddingService, LocalEmbeddingService


class _FakeOnnxFn:
    """Remplace ONNXMiniLM_L6_V2 : 384 dimensions, compte les instanciations."""

    instances = 0
    DOWNLOAD_PATH = None

    def __init__(self):
        type(self).instances += 1

    def __call__(self, texts):
        return [[float(len(t))] * 384 for t in texts]


@pytest.fixture
def fake_onnx(monkeypatch):
    import chromadb.utils.embedding_functions as ef

    _FakeOnnxFn.instances = 0
    monkeypatch.setattr(ef, "ONNXMiniLM_L6_V2", _FakeOnnxFn)
    return _FakeOnnxFn


class TestLocalEmbeddingService:
    def test_reports_real_model_metadata(self):
        service = LocalEmbeddingService()
        assert service.dimensions == 384
        assert service.model_name == "all-MiniLM-L6-v2"

    @pytest.mark.asyncio
    async def test_model_loads_lazily_and_only_once(self, fake_onnx):
        service = LocalEmbeddingService(model_dir="/tmp/does-not-matter")
        assert fake_onnx.instances == 0

        await service.generate_embedding("a")
        await service.generate_embedding("bb")

        assert fake_onnx.instances == 1
        assert fake_onnx.DOWNLOAD_PATH == "/tmp/does-not-matter"

    @pytest.mark.asyncio
    async def test_single_embedding_has_expected_shape(self, fake_onnx):
        vec = await LocalEmbeddingService().generate_embedding("hello")
        assert len(vec) == 384
        assert all(isinstance(x, float) for x in vec)

    @pytest.mark.asyncio
    async def test_batch_keeps_order(self, fake_onnx):
        vectors = await LocalEmbeddingService().generate_embeddings_batch(["a", "bb", "ccc"])
        assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_empty_batch_does_not_load_the_model(self, fake_onnx):
        assert await LocalEmbeddingService().generate_embeddings_batch([]) == []
        assert fake_onnx.instances == 0


class TestFactoryChoosesARealModel:
    def setup_method(self):
        factory.reset_services()

    def teardown_method(self):
        factory.reset_services()

    def test_no_openai_key_uses_local_model(self, monkeypatch):
        monkeypatch.setattr(factory.settings.llm, "openai_api_key", None)
        assert isinstance(factory.get_embedding_service(), LocalEmbeddingService)

    def test_openai_key_uses_openai(self, monkeypatch):
        monkeypatch.setattr(factory.settings.llm, "openai_api_key", "sk-test-not-real")
        assert isinstance(factory.get_embedding_service(), EmbeddingService)

    def test_openai_service_refuses_to_run_without_a_key(self):
        with pytest.raises(ValueError):
            EmbeddingService(api_key=None)
