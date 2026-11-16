"""Knowledge base endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import knowledge
from app.models import KnowledgeArticle, TicketType, User
from app.schemas import ArticleOut, ErrorOut

UNAUTHORIZED = {401: {"model": ErrorOut, "description": "Missing or invalid token"}}


def serialize(article: KnowledgeArticle, *, body: bool = True) -> ArticleOut:
    return ArticleOut(
        slug=article.slug,
        title=article.title,
        body=article.body if body else None,
        ticket_type=article.ticket_type,
        views=article.views,
    )


def build_router(get_session, current_user) -> APIRouter:
    router = APIRouter(prefix="/api/kb", tags=["knowledge base"], responses=UNAUTHORIZED)

    @router.get("", response_model=list[ArticleOut])
    def list_articles(
        q: str | None = Query(None, max_length=100, description="Words in the title or text"),
        type_filter: TicketType | None = Query(None, alias="type"),
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> list[ArticleOut]:
        """Most read first. Bodies are left out here; read one article for the text."""
        found = knowledge.search_articles(session, q, type_filter)
        return [serialize(a, body=False) for a in found]

    @router.get(
        "/{slug}",
        response_model=ArticleOut,
        responses={404: {"model": ErrorOut, "description": "No such article"}},
    )
    def read_article(
        slug: str,
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> ArticleOut:
        article = knowledge.read_article(session, slug)
        if article is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such article")
        return serialize(article)

    return router
