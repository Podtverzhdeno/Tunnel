"""Read-only, fail-closed incident projection of existing queue_monitor reports.

No Relay import, browser access, network, file mutation, message send, or retry.
This is an *advisory* incident classification: queue metadata is not proof of
Telegram delivery, model completion quality, or independent agent progress.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping

try:
    from queue_monitor import REASONS, STATES
except ImportError:
    # In package-style use without changing sys.path.
    from .queue_monitor import REASONS, STATES

FAILURE_STATES = frozenset({'blocked', 'uncertain'})
ACTIVE_STATES = frozenset({'queued', 'claimed', 'intent', 'saved'})
ALERT_CODES = frozenset({
    'failure_history_without_baseline', 'new_failures_since_snapshot',
    'stale_active_requests', 'response_age_target_exceeded',
})
UNAVAILABLE_CODES = frozenset({
    'database_unavailable', 'database_not_file', 'database_read_failed',
    'unsupported_queue_schema', 'unsupported_queue_record',
    'duplicate_request_identity', 'row_limit_exceeded',
    'queue_clock_in_future', 'baseline_from_future',
    'invalid_baseline', 'baseline_queue_mismatch',
    'baseline_too_large', 'invalid_row_limit',
    'invalid_stale_threshold', 'invalid_outcome_window',
    'invalid_latency_target', 'invalid_timestamp',
    'baseline_unavailable_or_invalid',
})
REASON_GROUPS = {
    'RATE_LIMIT': frozenset({'rate_limit'}),
    'AUTHENTICATION': frozenset({'login_required'}),
    'MANUAL_INTERVENTION': frozenset({'manual_input', 'user_disabled', 'owner_cancelled_stale_unsent'}),
    'UI_PRE_SEND': frozenset({
        'content_tab_not_ready', 'tab_create_unverified', 'tab_binding_unverified',
        'content_handoff_timeout', 'content_injection_failed',
        'content_runner_not_started', 'content_injection_unverified',
        'send_button_missing', 'button_absent', 'button_disabled',
        'button_not_scoped', 'editor_absent', 'editor_fill_failed',
        'editor_not_found', 'prompt_too_large',
    }),
    'POST_SEND_UNCERTAINTY': frozenset({
        'uncertain_send', 'send_unconfirmed_timeout', 'user_message_not_confirmed',
        'launch_ack_unverified', 'saved_tab_unverified',
        'reply_binding_mismatch', 'reply_unverified',
    }),
    'MODEL_OR_REPLY_WAIT': frozenset({
        'assistant_timeout', 'assistant_recovery_timeout', 'web_waiter_timeout',
        'reply_not_json', 'invalid_reply_content', 'empty_reply',
        'invalid_reply_tools', 'invalid_tool_arguments',
        'unoffered_or_invalid_tool', 'tools_disabled',
    }),
    'TRANSPORT_OR_OWNERSHIP': frozenset({
        'client_disconnected', 'claim_delivery_failed',
        'content_document_lost', 'content_document_binding_unverified',
        'launch_or_transport_unverified', 'relay_claim_unverified',
        'launch_fence_unverified', 'tab_binding_persist_failed',
        'recovery_archive_unverified', 'recovery_binding_missing',
        'recovery_release_unverified', 'orphan_tab_missing',
        'turn_binding_missing', 'draft_quarantine_unverified',
    }),
    'WORKER_OR_TASK': frozenset({
        'request_timeout', 'worker_failed', 'relay_shutting_down',
        'terminal_state_changed', 'missing_send', 'high_unverified',
        'content_start_unverified', 'reasoning_control_not_unique',
    }),
}
REASON_CLASS = {reason: group for group, codes in REASON_GROUPS.items() for reason in codes}
GAPS = ['worker_liveness_not_verified', 'telegram_delivery_not_verified',
        'browser_identity_not_verified', 'external_effects_not_reconciled',
        'exact_revision_verification_not_available']

class TriageError(ValueError):
    pass


def _nonnegative_int(value):
    if type(value) is not int or not 0 <= value <= 10000000:
        raise TriageError('invalid_count')
    return value


def _duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e10:
        raise TriageError('invalid_duration')
    return round(float(value), 3)


def _counts(value):
    if not isinstance(value, Mapping) or set(value) != STATES:
        raise TriageError('invalid_state_counts')
    return {state: _nonnegative_int(value[state]) for state in sorted(STATES)}


def _issue(code, kind, *, severity='P2', count=None, state=None, age=None, action='inspect', detail=None):
    record = {'code': code, 'kind': kind, 'severity': severity,
              'safe_next_step': action, 'verified_root_cause': False}
    if count is not None:
        record['count'] = _nonnegative_int(count)
    if state is not None:
        if state not in STATES:
            raise TriageError('invalid_incident_state')
        record['state'] = state
    if age is not None:
        record['oldest_request_age_seconds'] = _duration(age)
    if detail is not None:
        record['reason_code'] = detail
    return record


def _classify_failure(key, count):
    if not isinstance(key, str) or len(key) > 128 or ':' not in key:
        raise TriageError('invalid_failure_category')
    state, reason = key.split(':', 1)
    if state not in FAILURE_STATES or reason not in REASONS | {'none', 'other'}:
        raise TriageError('invalid_failure_category')
    group = REASON_CLASS.get(reason, 'UNCLASSIFIED_QUEUE_FAILURE')
    if state == 'uncertain':
        return _issue('EXTERNAL_EFFECT_UNCERTAIN', group, severity='P1', count=count,
                      state=state, detail=reason, action='reconcile_effect_without_replay')
    if group == 'POST_SEND_UNCERTAINTY':
        return _issue('POST_SEND_EVIDENCE_MISSING', group, severity='P1', count=count,
                      state=state, detail=reason, action='inspect_exact_turn_binding_no_replay')
    if group == 'AUTHENTICATION':
        action = 'verify_owner_authentication_without_secrets'
    elif group == 'RATE_LIMIT':
        action = 'verify_provider_retry_after_and_budgets'
    elif group == 'MANUAL_INTERVENTION':
        action = 'inspect_owner_intervention_before_resume'
    elif group == 'UI_PRE_SEND':
        action = 'check_browser_preflight_without_sending'
    else:
        action = 'compare_relay_browser_and_gateway_evidence'
    return _issue('QUEUE_FAILURE_REPORTED', group, severity='P2', count=count,
                  state=state, detail=reason, action=action)


def triage_report(report):
    """Summarize a queue_monitor report without copying private/untrusted strings.

    Returns a projection with a limited evidence scope and *no* permission to
    retry, reconnect, send, approve or conclude external effects.
    """
    if not isinstance(report, Mapping):
        raise TriageError('invalid_report')
    obs = report.get('observation')
    if obs == 'unavailable':
        code = report.get('error')
        if not isinstance(code, str) or code not in UNAVAILABLE_CODES:
            code = 'unclassified_observation_error'
        return {
            'schema_version': 1, 'status': 'observation_unavailable',
            'source_scope': 'queue_metadata_only', 'confidence': 'none',
            'issues': [_issue('NO_TRUSTED_QUEUE_SNAPSHOT', 'OBSERVER_UNAVAILABLE',
                              severity='P1', action='restore_read_only_observation')],
            'observation_error': code, 'safe_auto_recovery': False,
            'missing_evidence': list(GAPS),
        }
    if obs != 'available' or report.get('scope') != 'queue_metadata_only':
        raise TriageError('invalid_report_scope')
    counts = _counts(report.get('counts'))
    alerts = report.get('alerts')
    if not isinstance(alerts, list) or len(alerts) > 200:
        raise TriageError('invalid_alerts')
    issues = []
    for alert in alerts:
        if (not isinstance(alert, Mapping) or not isinstance(alert.get('code'), str)
                or alert.get('code') not in ALERT_CODES):
            raise TriageError('unsupported_alert')
        code = alert['code']
        if code == 'failure_history_without_baseline':
            issues.append(_issue('UNBASELINED_FAILURE_HISTORY', 'HISTORICAL_QUEUE_FAILURES',
                                 severity='P2', count=alert.get('count'),
                                 action='establish_local_baseline_and_review_history'))
        elif code == 'new_failures_since_snapshot':
            entries = alert.get('counts')
            if (not isinstance(entries, Mapping) or len(entries) > 256
                    or any(not isinstance(k, str) for k in entries)):
                raise TriageError('invalid_failure_counts')
            for key, count in sorted(entries.items()):
                issues.append(_classify_failure(key, _nonnegative_int(count)))
        else:
            states = alert.get('states')
            if (not isinstance(states, Mapping) or len(states) > len(STATES)
                    or any(not isinstance(k, str) for k in states)):
                raise TriageError('invalid_age_states')
            for state, detail in sorted(states.items()):
                if state not in ACTIVE_STATES or not isinstance(detail, Mapping):
                    raise TriageError('invalid_age_states')
                count = _nonnegative_int(detail.get('count'))
                age = _duration(detail.get('oldest_request_age_seconds'))
                if code == 'stale_active_requests':
                    issues.append(_issue('OLD_ACTIVE_REQUEST', 'AGE_NOT_PROOF_OF_WORKER_DEATH',
                                         severity='P2', count=count, state=state, age=age,
                                         action='compare_worker_heartbeat_and_phase_receipts'))
                else:
                    issues.append(_issue('AGE_TARGET_EXCEEDED', 'REQUEST_CREATION_AGE_ONLY',
                                         severity='P3', count=count, state=state, age=age,
                                         action='measure_actual_phase_latency_before_tuning'))
    # A baseline can suppress *new* failure alerts, but old UNCERTAIN records
    # still require external reconciliation; they must remain visible.
    if counts['uncertain'] and not any(issue['code'] == 'EXTERNAL_EFFECT_UNCERTAIN' for issue in issues):
        issues.append(_issue('UNCERTAIN_HISTORY_PRESENT', 'EFFECT_OUTCOME_NOT_PROVEN',
                             severity='P1', count=counts['uncertain'], state='uncertain',
                             action='inspect_all_uncertain_effects_without_replay'))
    if counts['blocked'] and not any(issue['code'] in {
            'QUEUE_FAILURE_REPORTED', 'POST_SEND_EVIDENCE_MISSING', 'UNBASELINED_FAILURE_HISTORY'
    } for issue in issues):
        issues.append(_issue('BLOCKED_HISTORY_PRESENT', 'PREVIOUS_REPORTED_FAILURE',
                             severity='P3', count=counts['blocked'], state='blocked',
                             action='compare_with_baseline_before_escalation'))
    severity_order = {'P1': 0, 'P2': 1, 'P3': 2}
    issues.sort(key=lambda x: (severity_order[x['severity']], x['code'], x.get('state', ''), x.get('reason_code', '')))
    return {
        'schema_version': 1,
        'status': 'attention_reported' if issues else 'no_queue_alerts_observed',
        'source_scope': 'queue_metadata_only',
        'confidence': 'queue_metadata_only',
        'counts': counts,
        'issues': issues,
        'safe_auto_recovery': False,
        'telegram_delivery_verified': False,
        'verified_task_completion': False,
        'missing_evidence': list(GAPS),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fail-closed read-only triage of sanitized queue_monitor output')
    parser.add_argument('--report', required=True, help='Existing JSON file from queue_monitor (never raw Relay SQLite)')
    args = parser.parse_args(argv)
    try:
        with open(args.report, 'rb') as f:
            body = f.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise TriageError('report_too_large')
        out = triage_report(json.loads(body))
    except (OSError, ValueError, TypeError, TriageError):
        out = {'schema_version': 1, 'status': 'observation_unavailable',
               'source_scope': 'queue_metadata_only', 'confidence': 'none',
               'issues': [_issue('INVALID_TRIAGE_INPUT', 'OBSERVER_UNAVAILABLE', severity='P1')],
               'safe_auto_recovery': False}
        print(json.dumps(out, ensure_ascii=True, sort_keys=True))
        return 2
    print(json.dumps(out, ensure_ascii=True, sort_keys=True))
    return 1 if out['issues'] else 0


if __name__ == '__main__':
    sys.exit(main())
