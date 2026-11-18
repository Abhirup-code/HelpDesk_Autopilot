"""The knowledge base: the pages people read instead of raising a ticket.

The articles live in the database so they can be searched and counted, and the
starter set below is written in once, so a fresh install is not an empty shelf.
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import KnowledgeArticle, TicketType

# Each one answers a question the help desk actually gets, in the words the person
# asking would use. Short on purpose: a page nobody finishes helps nobody.
STARTER_ARTICLES: list[tuple[str, str, TicketType | None, str]] = [
    (
        "forgot-my-password",
        "I forgot my password",
        TicketType.PASSWORD_RESET,
        "Raise a password reset request and a link arrives in your inbox within a few "
        "minutes. The link works once and expires after 15 minutes, so open it when you "
        "are at the computer you want to sign in on.\n\n"
        "Your new password needs at least 12 characters and cannot contain your username. "
        "A short phrase you will remember beats a short word you will not.",
    ),
    (
        "account-locked",
        "My account is locked after too many tries",
        TicketType.PASSWORD_RESET,
        "A lock clears on its own after 15 minutes. Waiting is usually faster than waiting "
        "for us.\n\n"
        "If you are still locked out after that, the saved password on another device is "
        "probably retrying in the background. Sign out of mail on your phone first, then "
        "raise a password reset request.",
    ),
    (
        "wifi-will-not-connect",
        "Wi-Fi will not connect",
        TicketType.HARDWARE,
        "Forget the network and join it again. That clears a saved password that no longer "
        "matches, which is behind most of these.\n\n"
        "If every network fails, restart the machine before raising a ticket. If only one "
        "room is affected, tell us which room: that is an access point problem and not "
        "yours.",
    ),
    (
        "laptop-will-not-charge",
        "My laptop will not charge",
        TicketType.HARDWARE,
        "Try a different outlet and a different cable before anything else. A dead brick "
        "and a dead outlet look identical from the desk.\n\n"
        "If the charger light is on and the battery still does not move, raise a hardware "
        "request and say which port you are using. Docks fail more often than chargers do.",
    ),
    (
        "printer-will-not-print",
        "The printer will not print",
        TicketType.HARDWARE,
        "Check that the job is not queued behind a stuck one. Open the print queue, cancel "
        "everything in it, and send one page again.\n\n"
        "If the queue is empty and nothing happens, you may be sending to a printer you "
        "cannot reach from your floor. Tell us the printer name on the label when you "
        "raise the ticket.",
    ),
    (
        "new-starter-first-day",
        "Getting a new starter ready",
        TicketType.NEW_HIRE,
        "Raise the new hire request as soon as the start date is agreed, not the week "
        "before. The account and group membership are created from the request, and the "
        "new person gets a link to set their own password.\n\n"
        "The link lasts 72 hours, so send it close to the start date. Their account is "
        "ready by nine on the morning they start.",
    ),
    (
        "someone-is-leaving",
        "Someone is leaving the company",
        TicketType.OFFBOARDING,
        "Raise the offboarding request with their last day as soon as you know it. "
        "Nothing happens until that day ends, so filing early is safe and filing late is "
        "not.\n\n"
        "After the last day their groups are removed, their password is cleared and the "
        "account is moved out of the active tree. Ask us separately about their mailbox "
        "and files: those are kept, not deleted.",
    ),
    (
        "asking-for-access",
        "Asking for access to a system",
        TicketType.ACCESS_REQUEST,
        "Access requests go to your manager, so pick the group you need and say what you "
        "will use it for. One line is enough and it is the line your manager reads.\n\n"
        "Admin groups need a second approval from IT. Nobody can approve their own "
        "request, including managers and IT, so ask a colleague if it is your own access.",
    ),
    (
        "what-the-priorities-mean",
        "What P1 to P4 mean, and when you will hear back",
        None,
        "P1 is one hour and P2 is four, counted on the clock. P3 is one working day and P4 "
        "is three, counted in weekdays.\n\n"
        "Those are targets for resolving the ticket, not for the first reply. If a ticket "
        "is stopping you working and it is not already P1 or P2, say so on the ticket and "
        "IT can raise it.",
    ),
]


def install_starter_articles(session: Session) -> int:
    """Add any starter article the database does not have yet. Returns how many."""
    have = set(session.scalars(select(KnowledgeArticle.slug)))
    added = 0
    for slug, title, ticket_type, body in STARTER_ARTICLES:
        if slug in have:
            continue
        session.add(KnowledgeArticle(slug=slug, title=title, ticket_type=ticket_type, body=body))
        added += 1
    if added:
        session.commit()
    return added


def search_articles(
    session: Session, query: str | None = None, ticket_type: TicketType | None = None
) -> list[KnowledgeArticle]:
    """Articles matching the words, or all of them. Most read first."""
    statement = select(KnowledgeArticle)
    if ticket_type is not None:
        statement = statement.where(KnowledgeArticle.ticket_type == ticket_type)
    words = (query or "").strip()
    if words:
        statement = statement.where(
            or_(
                KnowledgeArticle.title.icontains(words, autoescape=True),
                KnowledgeArticle.body.icontains(words, autoescape=True),
            )
        )
    return list(
        session.scalars(statement.order_by(KnowledgeArticle.views.desc(), KnowledgeArticle.title))
    )


def read_article(session: Session, slug: str) -> KnowledgeArticle | None:
    """One article, counting the read. Views are what tell you which pages earn their place."""
    article = session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug))
    if article is not None:
        article.views = (article.views or 0) + 1
        session.commit()
    return article


def suggestions_for(session: Session, ticket_type: TicketType, limit: int = 3) -> list:
    """The few articles worth showing before someone raises this kind of ticket."""
    statement = (
        select(KnowledgeArticle)
        .where(KnowledgeArticle.ticket_type == ticket_type)
        .order_by(KnowledgeArticle.views.desc(), KnowledgeArticle.title)
        .limit(limit)
    )
    return list(session.scalars(statement))


def article_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(KnowledgeArticle)) or 0
