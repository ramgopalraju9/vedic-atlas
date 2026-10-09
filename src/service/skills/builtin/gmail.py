"""Gmail tools: gmail_search, gmail_read, gmail_draft, gmail_send.

Mail goes through MailPort, so every call runs under SkillRunner (permission, rate limit, validation, audit) and the
allow-listed HTTP client. The safety design:

  * Reading is two-plane. `gmail_search` returns only senders and subjects, spoken verbatim by a template. `gmail_read`
    returns ONE body marked `returns: document`: it can only reach the content decode, never a control prompt.
  * Sending is two steps by construction. `gmail_draft` saves a draft and reads it back with its resolved address;
    `gmail_send` has no arguments and can send only that draft (DraftOutbox). The user hears exactly what will go out,
    and must then say so in a separate turn. The mail is never sent in the same turn it is composed.
  * A name ("Priya") is turned into an address from the user's own mail, and only when exactly one address matches.
    Otherwise the skill asks for the address; it never guesses a recipient.

The user's content is private: manifests set `private: true`, so results stay out of traces and logs.
"""

from __future__ import annotations

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.policies.mail_policy import (
    clean_text, is_address, message_has_content, pick_address, quote_for_query, spoken_draft, spoken_list,
)
from domain.ports.mail_port import MailPort
from exceptions.exception import AppException
from service.mail.draft_outbox import DraftOutbox
from service.skills.manifest_skill import ManifestSkill

_DEFAULT_SCOPE = "in:inbox"
_ADDRESS_LOOKUP_LIMIT = 10
_MAX_BODY_CHARS = 4000
_MAX_SUBJECT_CHARS = 150


class _MailSkill(ManifestSkill):
    def __init__(self, mail: MailPort, manifest: ToolManifest, **kw):
        super().__init__(manifest, **kw)
        self._mail = mail


class GmailSearchSkill(_MailSkill):
    """List recent emails: who from and the subject. Never bodies."""

    what = "check your email"

    async def run(
        self, ctx: AgentContext, query: str = "", unread_only: bool = False, folder: str = "", **_
    ) -> SkillResult:
        q = clean_text(query, 200)
        sent = folder == "sent"
        # The folder is a value from a fixed list that the code turns into the Gmail operator, so a model that writes
        # "in:inbox" or "from:me" for a question about sent mail cannot make the search look in the wrong place.
        scope = "in:sent" if sent else (_DEFAULT_SCOPE if folder == "inbox" or not q else "")
        terms = [t for t in (("is:unread" if unread_only else ""), scope, q) if t]
        messages = await self._mail.search(" ".join(terms))
        text = spoken_list(messages, unread_only=bool(unread_only) and not q, sent=sent)
        return self._ok(text, text)


class GmailReadSkill(_MailSkill):
    """Fetch ONE email (the newest match) as a document for the content decode to summarise."""

    what = "read that email"

    async def run(self, ctx: AgentContext, query: str = "", **_) -> SkillResult:
        found = await self._mail.search(clean_text(query, 200) or _DEFAULT_SCOPE, limit=1)
        if not found:
            return self._fail("no matching email", "I couldn't find a matching email.")
        msg = await self._mail.get(found[0].id)
        when = msg.received_at.strftime("%d %b %Y %H:%M") if msg.received_at else "unknown date"
        document = (
            f"EMAIL from {clean_text(msg.sender_label(), 60)} <{msg.sender_address}>, {when}\n"
            f"Subject: {clean_text(msg.subject, 120) or 'no subject'}\n\n{msg.body[:_MAX_BODY_CHARS] or msg.snippet}"
        )
        # `spoken` is only the fallback when summarising fails: it must never carry the body.
        fallback = f"The latest email is from {clean_text(msg.sender_label(), 40)}: {clean_text(msg.subject, 60) or 'no subject'}."
        return self._ok(document, fallback)


class GmailDraftSkill(_MailSkill):
    """Save a draft and read it back. Sends nothing."""

    what = "write that email"

    def __init__(self, mail: MailPort, manifest: ToolManifest, outbox: DraftOutbox, **kw):
        super().__init__(mail, manifest, **kw)
        self._outbox = outbox

    async def _resolve(self, to: str) -> tuple[str | None, str | None]:
        """(address, None) or (None, a spoken sentence saying what is needed)."""
        who = clean_text(to, 100)
        if is_address(who):
            return who, None
        if not who:
            return None, "Who should I send it to?"
        q = f"from:{quote_for_query(who)} OR to:{quote_for_query(who)}"
        found = pick_address(who, await self._mail.search(q, limit=_ADDRESS_LOOKUP_LIMIT))
        if len(found) == 1:
            return found[0], None
        if not found:
            return None, f"I couldn't find an email address for {who}. What is it?"
        return None, f"I found more than one address for {who}: {', '.join(found[:3])}. Which one?"

    async def run(self, ctx: AgentContext, to: str = "", subject: str = "", body: str = "", **_) -> SkillResult:
        text = (body or "").strip()
        if not text or not message_has_content(ctx.user_message, to):
            return self._fail("nothing to say", "What should the email say?")
        address, ask = await self._resolve(to)
        if address is None:
            return self._fail("recipient unknown", ask)
        clean_subject = clean_text(subject, _MAX_SUBJECT_CHARS) or "No subject"
        draft = await self._mail.create_draft(address, clean_subject, text[:_MAX_BODY_CHARS])
        self._outbox.put(draft, ctx.session_id)
        spoken = spoken_draft(address, clean_subject, text)
        return self._ok(f"Draft saved to {address}: {clean_subject}", spoken)


class GmailSendSkill(_MailSkill):
    """Send the draft that was just read back. Takes no arguments, so it can send nothing else."""

    what = "send that email"

    def __init__(self, mail: MailPort, manifest: ToolManifest, outbox: DraftOutbox, **kw):
        super().__init__(mail, manifest, **kw)
        self._outbox = outbox

    async def run(self, ctx: AgentContext, **_) -> SkillResult:
        draft = self._outbox.take(ctx.session_id)   # removed first: a failed or repeated send can never double-send
        if draft is None:
            return self._fail(
                "no draft to send", "There's no draft waiting to be sent. Ask me to write one first.",
            )
        try:
            await self._mail.send_draft(draft.id)
        except AppException as e:
            # Whether Google received it is unknown after a timeout, so the draft is NOT re-armed: say where it is.
            return self._fail(e.message, "I couldn't confirm that it was sent. The draft is in your Gmail drafts.")
        text = f"Sent to {draft.to}: {clean_text(draft.subject, 80)}."
        return self._ok(text, text)
