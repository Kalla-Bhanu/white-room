from __future__ import annotations

import sqlite3

import typer

from core.chat import (
    create_conversation,
    get_or_create_first,
    list_conversations,
    save_message,
)


chat_app = typer.Typer(help="Conversation CRUD commands.")


@chat_app.command("new")
def chat_new(
    slug: str,
    title: str = typer.Option(..., "--title"),
    lane: str = typer.Option("deterministic", "--lane"),
) -> None:
    """Create a conversation and its first session."""
    try:
        conversation, session = create_conversation(slug, title, lane=lane)
    except (ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"conversation {conversation.id} created for {slug} with session {session.id} "
        f"(lane={session.lane})"
    )


@chat_app.command("list")
def chat_list(slug: str) -> None:
    """List conversations for a project."""
    try:
        conversations = list_conversations(slug)
    except (ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not conversations:
        typer.echo("no conversations")
        return

    for conversation in conversations:
        archived = "archived" if conversation.archived else "active"
        typer.echo(
            f"{conversation.id}\t{conversation.title}\t{archived}\t"
            f"sessions={conversation.session_count}\tmessages={conversation.message_count}\t"
            f"{conversation.updated_at}"
        )


@chat_app.command("message")
def chat_message(
    slug: str,
    content: str = typer.Option(..., "--content"),
    role: str = typer.Option("user", "--role"),
    conversation_id: int | None = typer.Option(None, "--conversation-id"),
    session_id: int | None = typer.Option(None, "--session-id"),
) -> None:
    """Save a local chat message to the latest conversation/session."""
    try:
        if conversation_id is None:
            conversation, session = get_or_create_first(slug)
            conversation_id = conversation.id
            session_id = session.id
        record = save_message(
            conversation_id=conversation_id,
            session_id=session_id,
            role=role,
            content=content,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"message {record.id} saved for conversation {record.conversation_id} (tokens={record.token_estimate})")


@chat_app.command("first")
def chat_first(slug: str) -> None:
    """Return the first conversation, creating it when needed."""
    try:
        conversation, session = get_or_create_first(slug)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"conversation {conversation.id} session {session.id} title={conversation.title}")
