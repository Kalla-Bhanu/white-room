# Product Tour

This tour uses screenshots captured from a clean local demo runtime. The screenshots intentionally avoid private project memory, API keys, personal paths, account dashboards, and real conversations.

![WHITE ROOM product tour](assets/white-room-product-tour.gif)

## 1. Cockpit Chat

The cockpit keeps the conversation centered while lanes, modes, task packet controls, and provider availability remain visible. The default workflow is local-first: chat, attach scoped memory, choose a lane, and keep route context nearby.

![Cockpit chat](assets/screenshots/cockpit-chat.png)

## 2. Task Packet Board

The board turns a project plan into task packets. This is the core cost-control mechanism: a high-capability model can produce a plan, then lower-cost lanes can execute scoped packets with less repeated context.

![Task packet board](assets/screenshots/board-command-surface.png)

## 3. Route Explorer

The route explorer is where route decisions, candidate lanes, approval pressure, and fallback explanations become inspectable. In a fresh demo it starts empty; real routed turns populate this view.

![Route explorer](assets/screenshots/route-explorer.png)

## 4. Provider Settings

Provider settings are presence-only. The UI shows whether a key exists, but does not render raw values. Dashboard URLs are rejected or normalized to API base URLs.

![Provider settings](assets/screenshots/provider-settings.png)

## 5. Endpoint Registry

The endpoint registry shows local, manual, cloud, and custom gateway lanes in one place. This is the routing surface that lets a user decide which work belongs on local models, manual sessions, custom gateways, or cloud APIs.

![Endpoint registry](assets/screenshots/endpoint-registry.png)

## 6. Runner Health

The runner health page checks local model availability separately from cloud providers. Localhost runners are treated as local capability, while remote lanes remain explicit and gated.

![Runner health](assets/screenshots/runner-health.png)

## 7. Usage Monitor

Usage monitoring keeps token and cost values visible as estimates. This makes cost pressure part of the workflow instead of a surprise after the work is done.

![Usage monitor](assets/screenshots/usage-monitor.png)
