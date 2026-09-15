# Client offboarding

- [ ] Confirm the client ID, exact Task and all schedules/integrations that can launch it. Record the requested stop date.
- [ ] Disable its schedules and remove the Task from any shared schedule. Stop manual/API launch paths; wait for active runs to finish or abort them as appropriate. Disabling a schedule does not stop an already running Actor. Apify Tasks are saved configurations: do not assume a Task has a separate enable/disable switch.
- [ ] Export useful client-scoped Dataset results if requested. Verify the export contains only that customer and transfer through the agreed channel. Note run IDs and export dates.
- [ ] Apply the agreed retention/deletion policy to saved input, historical runs/inputs, Dataset items, snapshots, monitor state, exports and operator records. Do not invent a retention period. If no policy is agreed, settle it with the responsible owner before deleting.
- [ ] For approved snapshot deletion, identify every URL ever monitored for this client (including removed URLs). Verify each `snapshot-*` record's `client_id` and `url`, then remove only that record and its paired `monitor-*` record. Follow the [single-URL procedure](OPERATOR_RUNBOOK.md#reinitialize-one-url-safely) for key verification. Never delete the shared KVS to offboard one client.
- [ ] Remove/archive the client Task and its schedule as agreed; clear its recipient/configuration and client-specific integrations or credentials. Preserve shared Resend credentials, sender domain and `OPERATOR_EMAIL` used by other clients. Previously stored inputs and exports require separate handling; deleting a Task is not proof all historical data is gone.
- [ ] Include the client-scoped `weekly-*` activity/report record in retention or deletion. Verify its stored `client_id` before changing it; do not delete other customers' report history.
- [ ] Confirm monitoring has stopped, record what was retained/deleted and why, and manually acknowledge offboarding. Check there is no upcoming scheduled run for this customer. Record the retired client ID so it is not inadvertently reassigned with old state.

Removing a customer's URL or Task must not change any other customer's URLs, storage or schedule. See [Apify record-level storage operations](https://docs.apify.com/api/v2/storage-key-value-stores) for targeted deletion; use record deletion, not store deletion.
