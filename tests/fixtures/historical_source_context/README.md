# Historical reports before reviewed source context

Generated from commit `4e987fe271ba5862120e013903754cd3365f5d9e` before any
runtime change. The input is the synthetic sentence
`Nova должна создавать ВМ через API за одну миллисекунду`.

Generation used `tests.agent_support.make_agent_config`, `AgentService`,
`tests.test_agent_session.start`, `select_source` and `insufficient_proposal`
from `tests.test_binding_sessions`. For each legacy/deep profile: start a
texts session, accept the canonical atoms at revision 0, submit the explicit
insufficient proposal at revision 1, finalize at revision 2 and call get_result.
The deep report permits partial export because the synthetic empty selection
does not complete its responsibility graph. Finalization, rather than a
positive status or run success, is the historical compatibility contract.

Each directory preserves all five artifact bytes, `hashes.json` with their
SHA-256 values, and `state.json` with the four sealed SQLite tables (sessions,
events, publications, receipts). The result schemas are legacy 1.1/deep 2.1.
The transient test signing key is not included. There are no private workbook
texts, credentials or operational trust settings in these fixtures.

Restore the four tables in a temporary AgentStore and copy the five artifacts
to the publication's recorded final_name under the temporary output_root.
Historical reads must validate the original hashes without running the new
resolver. Do not regenerate these fixtures with the new runtime.

`deep20` generated from commit `434c76e385bda3d988947b33b522a228ed59fcb5` in a temporary archive checkout. Original schema 2.0, finalized with allow_partial=true for one synthetic unanalysed row. Original artifact bytes and seals preserved; no signing keys included.
