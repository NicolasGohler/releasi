# Archive and permanent deletion

Archive is the normal list-removal action. Memberships, campaign assignments,
notes, and action history are retained; restoring the list restores visibility.
Restoring never activates a campaign or changes a lead's status.

Normal lead-library and campaign lead listings require membership in at least
one non-archived list. A non-archived campaign alone does not preserve visibility.
List-less historical leads are hidden rather than automatically deleted.
Campaign counts and the global activity feed exclude these hidden leads.
Dispatch candidate queries and the scheduler's pre-execution check exclude them
as well. An action already executing when archive is requested cannot be undone.
Daily safety/rate-limit accounting remains unchanged: archiving history must not
make previously sent messages disappear from the account's send budget.

Collection APIs exclude archived objects by default. `include_archived=true`
explicitly includes them. Fetching an archived object's ID remains supported;
targeted archived list/campaign views can display their retained members.
Nested relationships omit archived lists/campaigns/accounts unless requested.

The UI offers permanent deletion only for archived lists, with confirmation.
The DELETE API remains a permanent deletion operation. Contacts with any other
list membership, including an archived list, survive and lose only the deleted
list association. Contacts with no other list are deleted along with notes,
events, campaign assignments, action logs, and broadcast membership. Cleanup and
affected relationship-counter updates are committed together or rolled back.

The 48 contacts retained during the Institutional Rails cleanup were pre-existing
shared contacts. All 48 had non-archived memberships at investigation time on
2026-10-09. No further production list or lead deletion is part of this release.
