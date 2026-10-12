# Agents workspace

`/agents` keeps three records visually separate:

| Section | What it is | What it is not |
| --- | --- | --- |
| Runtime identities | The durable registry shared with Planning and the office | A simulated roster |
| Catalog boundary | Imported specialist and skill definitions, including the existing review controls | A registered runtime identity |
| Demonstration agents | Seeded simulator roles, workload, and temporary restricted agents | Identities that can be assigned planning work |

Demonstration search, status, department, and permanent/temporary filters read the current snapshot only. They do not call the API. Simulated progress and simulated reliability are the values already stored on the demonstration agent. They are labeled as simulation so they are not read as runtime performance.

Creating a temporary agent still posts only to `/api/agents/temporary`. The page says the result is not a runtime identity. Registration, activation, and capability assignment stay in the existing identity registry and are unchanged.

The shared agent detail drawer still opens from each demonstration card.

Temporary names accept 2–60 characters and roles accept 2–100, matching `CreateTemporaryAgentRequest`. The demonstration navigation target and its accessible heading have distinct IDs.

A selected recorded status stays visible when it disappears from a later snapshot, so the displayed filter continues to match the filtered results.
