# WHITE ROOM Playbook

## Daily Start
1. Check `projects/white-room/brain/current_status.md`.
2. Review `projects/white-room/brain/tasks.md` for the next open item.
3. Read the latest handoff in `projects/white-room/brain/handoffs.md`.

## Working Loop
1. Create or update exactly one packet.
2. Keep changes inside the packet's scope.
3. Run the packet verification commands.
4. Append a handoff and update current status.
5. Commit the completed packet.

## Reindex
Use `python -m cli.main reindex` if you need to rebuild SQLite from the project files and current snapshot.

## Export
Use `python -m cli.main export-project <slug> --to <zip>` to package a project folder. Extract the archive and run `reindex` to rebuild the index from files.

## Guardrails
- Keep the app local-first.
- Do not add cloud calls before the plan permits them.
- Keep `brain/` files as the source of truth.
