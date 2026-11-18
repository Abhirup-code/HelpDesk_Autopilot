"""The knowledge base: the pages people read instead of raising a ticket."""

from app.knowledge import STARTER_ARTICLES, article_count, install_starter_articles
from app.models import TicketType
from tests.conftest import TOKENS


def auth(name):
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def test_a_fresh_install_has_the_starter_articles(client, app):
    with app.state.session_factory() as session:
        assert article_count(session) == len(STARTER_ARTICLES)


def test_starting_twice_does_not_duplicate_them(app):
    with app.state.session_factory() as session:
        before = article_count(session)
        assert install_starter_articles(session) == 0
        assert article_count(session) == before


def test_a_missing_article_is_put_back(app):
    from sqlalchemy import select

    from app.models import KnowledgeArticle

    with app.state.session_factory() as session:
        gone = session.scalar(select(KnowledgeArticle))
        slug = gone.slug
        session.delete(gone)
        session.commit()
        assert install_starter_articles(session) == 1
        assert session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug))


def test_the_list_leaves_the_text_out(client):
    articles = client.get("/api/kb", headers=auth("emma")).json()
    assert len(articles) == len(STARTER_ARTICLES)
    assert all(a["body"] is None for a in articles)


def test_reading_one_article_returns_its_text(client):
    article = client.get("/api/kb/forgot-my-password", headers=auth("emma")).json()
    assert article["title"] == "I forgot my password"
    assert "expires after 15 minutes" in article["body"]


def test_an_unknown_article_is_a_404(client):
    assert client.get("/api/kb/no-such-page", headers=auth("emma")).status_code == 404


def test_searching_matches_the_title_and_the_text(client):
    by_title = client.get("/api/kb?q=printer", headers=auth("emma")).json()
    assert [a["slug"] for a in by_title] == ["printer-will-not-print"]
    by_body = client.get("/api/kb?q=access point", headers=auth("emma")).json()
    assert [a["slug"] for a in by_body] == ["wifi-will-not-connect"]


def test_searching_ignores_capitals(client):
    assert client.get("/api/kb?q=PRINTER", headers=auth("emma")).json()


def test_filtering_by_ticket_type(client):
    found = client.get("/api/kb?type=password_reset", headers=auth("emma")).json()
    slugs = {a["slug"] for a in found}
    assert slugs == {"forgot-my-password", "account-locked"}


def test_reading_an_article_counts_the_read(client):
    for _ in range(3):
        client.get("/api/kb/account-locked", headers=auth("emma"))
    listed = client.get("/api/kb", headers=auth("emma")).json()
    assert next(a for a in listed if a["slug"] == "account-locked")["views"] == 3


def test_the_most_read_article_comes_first(client):
    for _ in range(5):
        client.get("/api/kb/printer-will-not-print", headers=auth("emma"))
    listed = client.get("/api/kb", headers=auth("emma")).json()
    assert listed[0]["slug"] == "printer-will-not-print"


def test_the_knowledge_base_needs_a_signed_in_user(client):
    assert client.get("/api/kb").status_code == 401


def test_every_starter_article_points_at_a_real_ticket_type():
    for _, _, ticket_type, _ in STARTER_ARTICLES:
        assert ticket_type is None or isinstance(ticket_type, TicketType)
