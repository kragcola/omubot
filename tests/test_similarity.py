from services.similarity import NgramSimilarityProvider, create_similarity_provider, normalize_text_key


def test_ngram_similarity_normalizes_markdown_punctuation() -> None:
    provider = NgramSimilarityProvider()

    assert normalize_text_key("**猫 饼！**") == "猫饼"
    assert provider.similarity("猫饼", "**猫 饼！**") == 1.0
    assert provider.similarity("猫饼", "猫饼行为") >= 0.8


def test_embedding_backend_is_safe_stub() -> None:
    provider = create_similarity_provider("embedding")

    try:
        provider.similarity("a", "b")
    except RuntimeError as exc:
        assert "not installed" in str(exc)
    else:
        raise AssertionError("embedding backend should fail closed when unavailable")
