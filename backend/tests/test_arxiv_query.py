"""Regression tests for the arXiv query builder (long keywords must match)."""
from app.sources.arxiv import _ARXIV_API, _build_search_query, _term_clause


def test_endpoint_is_https_so_no_301_downgrade():
    # http:// 301-redirects and httpx does not follow by default -> silent fixture fallback.
    assert _ARXIV_API.startswith("https://")


def test_long_keyword_gets_an_and_of_words_fallback():
    clause = _term_clause("protein language model latent space")
    # The bare exact-phrase form matches zero papers; an AND-of-words form must be OR'd in.
    assert 'all:"protein language model latent space"' in clause
    assert "AND" in clause
    assert clause.startswith("(") and clause.endswith(")")


def test_single_word_stays_a_plain_phrase():
    assert _term_clause("proteomics") == 'all:"proteomics"'


def test_stopwords_dropped_from_the_and_clause():
    clause = _term_clause("why is there no tool for the lab")
    assert 'all:"the"' not in clause and 'all:"why"' not in clause


def test_terms_are_ored_together():
    q = _build_search_query(["surgical robotics", "cell simulation"])
    assert " OR " in q


def test_empty_terms_fall_back_to_a_valid_query():
    assert _build_search_query([]) == "all:artificial intelligence"
