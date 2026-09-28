# Protect and package the wake classifier's transitive dependency

Status: proposed implementation; requires PR review and the final package gates.

BridgeEventClassifier.ps1 unconditionally imports BridgeWakeClass.ps1. Readers
and the event writer import the classifier, so its new dependency must receive
the same bridge_protocol self-modification protection. Leaving the imported
module unprotected would defeat protection of the classifier itself.

Add the module to the denylist, supervisor's exact watcher dependency set and
its bundled configuration together. The consumer inventory identifies this as
an internal implementation, not a measured delivery consumer. Regression tests
require agreement and a materialized import target.

Deploy only an independently verified complete bundle, never these files one
at a time. Missing, modified or mismatched dependencies must retain existing
fail-closed behavior. This change grants no runtime self-modification, rollout,
model switching, installation or unfreeze authority. Existing HOLDs remain.

The final package still needs actual bundle/install and consumer acceptance,
both RCO reviews and the single operator signature. Static closure tests alone
are not live delivery or deployment evidence.
