from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from flask import Flask, abort, redirect, render_template_string, request, url_for

from bellhaven.actions import execute, rollback
from bellhaven.config import (DATA_DIR, DATABASE_PATH, SOURCE_BASE_URL, SOURCE_NAME,
                              TARGET_PARENT_ACCOUNT_NAME, TARGET_PARENT_NAME)
from bellhaven.crm import CRMClient
from bellhaven.normalize import clean, normalize_name, normalize_phone, normalize_street, normalize_zip
from bellhaven.store import ReviewStore
from bellhaven.matcher import build_proposals
from bellhaven.scraper import candidate_names_for_parent, scrape_communities
from bellhaven.source_validation import validate_public_https_url

app = Flask(__name__)

NEW_RUN_PAGE = r"""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>New reconciliation</title>
<style>
body{font-family:Inter,ui-sans-serif,system-ui;margin:0;background:#f6f7f3;color:#19231f}.wrap{max-width:900px;margin:auto;padding:35px 24px}.panel{background:white;border:1px solid #dce5df;border-radius:14px;padding:24px;margin:16px 0}h1{margin:0 0 6px}p{color:#65736d}label{display:block;font-size:12px;font-weight:700;margin:17px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#65736d}input,select{width:100%;padding:11px 12px;border:1px solid #cfdad3;border-radius:7px;font:inherit}button,.button{display:inline-block;border:0;border-radius:7px;padding:11px 17px;font-weight:700;text-decoration:none;cursor:pointer;margin:18px 8px 0 0}.primary{background:#245c4c;color:white}.secondary{background:#e8f1ed;color:#245c4c}.error{background:#f8e5e5;color:#842f2f;padding:12px 15px;border-radius:8px}.notice{background:#fff6dc;color:#6e4b0f;padding:12px 15px;border-radius:8px}.preview{overflow:auto}.preview table{border-collapse:collapse;width:100%;font-size:13px}.preview th,.preview td{text-align:left;border-bottom:1px solid #e4eae6;padding:8px}.preview th{color:#65736d}.actions{display:flex;gap:8px;align-items:center}
</style></head><body><main class="wrap"><a href="/?run={{current_run_id}}">← Back to review</a><h1>New reconciliation</h1><p>Select a CRM parent and test an operator website before generating any proposals. Nothing in this flow writes to CRM.</p>
{% if error %}<div class="error">{{error}}</div>{% endif %}{% if preview %}<div class="notice">Source test passed: {{preview|length}} facilities found. Review the sample before creating the run.</div>{% endif %}
<form class="panel" method="post">
<label>CRM parent company</label><select name="parent_id" required>{% for parent in parents %}<option value="{{parent.account_id}}" {{'selected' if values.parent_id==parent.account_id else ''}}>{{parent.name}}</option>{% endfor %}</select>
<label>Evidence source name</label><input name="source_name" required value="{{values.source_name}}" placeholder="e.g. Cedar Trail Communities official website">
<label>Operator website base URL</label><input type="url" name="source_url" required value="{{values.source_url}}" placeholder="https://operator.example.com">
<p>This demo adapter expects a <code>/communities</code> directory and facility detail pages with the assessment site's field structure. Unsupported sources fail safely before proposals are created.</p>
<div class="actions"><button class="secondary" name="action" value="test">Test source</button><button class="primary" name="action" value="run" onclick="return confirm('Create a new reconciliation run from this parent and source? No CRM changes will be written yet.')">Run reconciliation</button></div></form>
{% if preview %}<section class="panel preview"><h2>Extraction preview</h2><table><thead><tr><th>Name</th><th>Address</th><th>Care offerings</th><th>Administrator</th><th>Phone</th></tr></thead><tbody>{% for item in preview[:10] %}<tr><td>{{item.name}}</td><td>{{item.street}}, {{item.city}} {{item.state}} {{item.zip}}</td><td>{{item.care_offerings|join(', ')}}</td><td>{{item.administrator}}</td><td>{{item.phone}}</td></tr>{% endfor %}</tbody></table>{% if preview|length > 10 %}<p>Showing 10 of {{preview|length}} facilities.</p>{% endif %}</section>{% endif %}
</main></body></html>
"""

PAGE = r"""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CRM Reconciliation Review</title>
<style>
:root{--green:#245c4c;--ink:#19231f;--muted:#65736d;--line:#dce5df;--cream:#f6f7f3;--amber:#9b6513;--red:#963d3d}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui;margin:0;background:var(--cream);color:var(--ink)}
.top{background:var(--green);color:white}.wrap{max-width:1180px;margin:auto;padding:26px 28px}.top h1{margin:0 0 5px;font-size:27px}.top p{margin:0;opacity:.8}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}.metric{background:white;border:1px solid var(--line);border-radius:10px;padding:12px 18px;min-width:105px}.metric b{font-size:22px;display:block}.metric span{font-size:12px;color:var(--muted)}
.context{background:white;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:20px 0 12px}.context-head{display:flex;align-items:center;justify-content:space-between;gap:15px}.context h2{font-size:15px;margin:0 0 5px}.run-controls{display:flex;align-items:center;gap:8px}.run-controls select{max-width:330px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:white}.new-run{font-size:12px;text-decoration:none;background:var(--green);color:white;padding:8px 11px;border-radius:7px;white-space:nowrap}.context-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.context-item span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.context-item b,.context-item a{font-size:13px;color:var(--ink)}.scope-note{font-size:12px;color:var(--muted);margin:10px 0 0;max-width:90ch}
.filter-box{background:white;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:12px}.filter-head{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}.filter-head h2{font-size:14px;margin:0}.filter-head span{font-size:12px;color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap}.filters a{text-decoration:none;color:var(--ink);background:#fafbf9;border:1px solid var(--line);border-radius:20px;padding:7px 12px;font-size:13px}.filters a.active{background:var(--green);color:white;border-color:var(--green)}.filter-count{font-size:11px;opacity:.7;margin-left:5px}.showing{font-size:13px;color:var(--muted);margin:18px 2px 8px}
.card{background:white;border:1px solid var(--line);border-radius:14px;padding:23px;margin:16px 0;box-shadow:0 2px 8px #183b2e0a;scroll-margin-top:18px}.card.chow{border-left:6px solid #b47118}.card.duplicate{border-left:6px solid #8b5b91}.card.create{border-left:6px solid #2876a8}.card.missing_from_website{border-left:6px solid #a14949}
.eyebrow{display:flex;gap:8px;align-items:center}.badge{font-size:11px;text-transform:uppercase;letter-spacing:.05em;background:#e8f1ed;border-radius:12px;padding:4px 9px}.confidence.high{background:#dff2e6;color:#205c39}.confidence.medium{background:#fff0c7;color:#77500e}.confidence.low{background:#f5dddd;color:#842f2f}.confidence.review{background:#e8e5f5;color:#57488a}.card h2{font-size:22px;line-height:1.2;margin:12px 0 5px;max-width:42ch}.match-subtitle{font-size:13px;color:#3f514a;margin:0 0 3px}.match-subtitle code{font-size:11px;background:#eef2ef;padding:2px 5px;border-radius:4px}.reason{color:var(--muted);font-size:12px;margin:0 0 16px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}.cols.three{grid-template-columns:repeat(3,1fr)}.panel{background:#f8faf8;border:1px solid #e5ebe7;border-radius:10px;padding:15px}.panel.keep{background:#eef8f2;border-color:#bddbc8}.panel.remove{background:#fff7f4;border-color:#e8c8bd}.panel h3{margin:0 0 11px;font-size:14px}.field{display:grid;grid-template-columns:120px 1fr;gap:10px;padding:6px 8px;border-bottom:1px solid #e8ece9;font-size:13px;border-radius:5px}.field:last-child{border:0}.label{color:var(--muted)}
.field.mismatch{background:#fff0e8;border-bottom-color:#f2c7b2}.field.mismatch .label{color:#9a4328;font-weight:700}.field.mismatch .label:before{content:"●";font-size:8px;margin-right:6px;color:#d05c38}.field.mismatch .value{color:#782d18;font-weight:700}
.account-id{font-size:11px;color:var(--muted);margin:-5px 0 9px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.source-meta{font-size:11px;color:var(--muted);margin:9px 8px 0}.source-meta a{color:var(--green)}.compare-note{font-size:12px;color:#8b472f;margin:8px 0 0}.compare-note b{display:inline-block;background:#fff0e8;border-radius:4px;padding:2px 7px;margin-right:5px}
.changes{margin:16px 0;background:#fffaf0;border:1px solid #eeddb5;border-radius:10px;padding:14px 16px}.changes h3{font-size:14px;margin:0 0 8px}.changes li{margin:6px 0;font-size:14px}.old{text-decoration:line-through;color:#8a9490}.arrow{padding:0 7px;color:var(--amber)}.new{font-weight:650}
.actions{display:flex;align-items:center;gap:10px}.actions button{padding:10px 16px;border:0;border-radius:7px;font-weight:650;cursor:pointer}.approve{background:var(--green);color:white}.reject{background:#f3e8e8;color:#7b2e2e}.undo{background:#fff0c7;color:#6e4b0f}.decision{font-weight:700;text-transform:uppercase;font-size:12px;color:var(--muted)}.audit-time{font-size:11px;color:#7b8782;margin:8px 0 0 2px}
.bulk-box{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;background:#eef8f2;border:1px solid #bddbc8;border-radius:12px;padding:14px 18px;margin:16px 0}.bulk-box.review-bulk{background:#f1eff9;border-color:#d7d0ed}.bulk-box h2{font-size:14px;margin:0 0 3px}.bulk-box p{font-size:12px;color:var(--muted);margin:0}.bulk-box details{margin-top:9px;font-size:12px}.bulk-box summary{cursor:pointer;color:var(--green);font-weight:650}.bulk-box.review-bulk summary,.bulk-box.review-bulk .bulk-list a{color:#57488a}.bulk-list{margin:7px 0 0;padding-left:20px}.bulk-list li{margin:4px 0}.bulk-list a{color:var(--green)}.bulk-box button{white-space:nowrap;padding:10px 16px;border:0;border-radius:7px;background:var(--green);color:white;font-weight:650;cursor:pointer}.bulk-box.review-bulk button{background:#57488a}.bulk-result{border-radius:9px;padding:10px 14px;margin:12px 0;font-size:13px}.bulk-result.ok{background:#e2f3e8;color:#205c39}.bulk-result.warn{background:#fff0c7;color:#6e4b0f}
@media(max-width:900px){.cols,.cols.three,.context-grid{grid-template-columns:1fr}.field{grid-template-columns:110px 1fr}.wrap{padding:20px 16px}}
</style></head><body>
<header class="top"><div class="wrap"><h1>{{context.target_parent_name}} CRM Reconciliation</h1><p>Review the evidence. Nothing writes to CRM without your approval.</p></div></header>
<main class="wrap">
<section class="context"><div class="context-head"><h2>Reconciliation context</h2><div class="run-controls"><form method="get"><input type="hidden" name="decision" value="{{decision}}"><input type="hidden" name="kind" value="{{kind}}"><select name="run" onchange="this.form.submit()" aria-label="Reconciliation run">{% for item in runs %}<option value="{{item.run_id}}" {{'selected' if item.run_id==run_id else ''}}>{{item.target_parent_name}} · {{item.source_name}}</option>{% endfor %}</select></form><a class="new-run" href="/runs/new?from={{run_id}}">New reconciliation</a></div></div><div class="context-grid">
<div class="context-item"><span>Target parent company</span><b>{{context.target_parent_name}}</b><small class="account-id">{{context.target_parent_account_id}}</small></div>
<div class="context-item"><span>Operator evidence source</span><a href="{{context.source_base_url}}" target="_blank" rel="noreferrer">{{context.source_name}}</a><small class="account-id">{{context.source_base_url}}</small></div>
<div class="context-item"><span>Website data collected at</span><b>{{context.website_collected_display}}</b></div>
<div class="context-item"><span>CRM snapshot collected at</span><b>{{context.crm_collected_display}}</b></div></div>
<p class="scope-note">The operator website is the source of truth for facilities currently claimed by this parent company. It does not by itself prove that a differently owned CRM account is the same legal or operational entity.</p></section>
<div class="summary"><div class="metric"><b>{{counts.total}}</b><span>Total proposals</span></div><div class="metric"><b>{{counts.pending}}</b><span>Pending</span></div><div class="metric"><b>{{counts.approved}}</b><span>Approved</span></div><div class="metric"><b>{{counts.rejected}}</b><span>Rejected</span></div><div class="metric"><b>{{counts.withdrawn}}</b><span>Withdrawn</span></div></div>
<section class="filter-box"><div class="filter-head"><h2>Review status</h2><span>Where each proposal is in the approval workflow</span></div><div class="filters">
{% for value,label in [('pending','Pending'),('approved','Approved'),('rejected','Rejected'),('withdrawn','Withdrawn'),('all','All statuses')] %}<a class="{{'active' if decision==value else ''}}" href="/?run={{run_id}}&decision={{value}}{% if kind %}&kind={{kind}}{% endif %}">{{label}} <span class="filter-count">{{counts[value] if value != 'all' else counts.total}}</span></a>{% endfor %}</div></section>
<section class="filter-box"><div class="filter-head"><h2>Change type</h2><span>What kind of CRM action is being proposed</span></div><div class="filters">
<a class="{{'active' if not kind else ''}}" href="/?run={{run_id}}&decision={{decision}}">All types <span class="filter-count">{{type_counts.total}}</span></a>
{% for value,label in [('safe_match','Safe match'),('chow','CHOW'),('duplicate','Duplicate'),('create','New account'),('missing_from_website','Missing from website'),('update','Update')] %}<a class="{{'active' if kind==value else ''}}" href="/?run={{run_id}}&decision={{decision}}&kind={{value}}">{{label}} <span class="filter-count">{{type_counts[value]}}</span></a>{% endfor %}</div></section>
{% if bulk_ok %}<div class="bulk-result ok">Approved {{bulk_ok}} safe match{{'' if bulk_ok == 1 else 'es'}}.{% if bulk_failed %} {{bulk_failed}} failed and remain Pending.{% endif %}</div>{% elif bulk_failed %}<div class="bulk-result warn">No records were approved; {{bulk_failed}} failed and remain Pending.</div>{% endif %}
{% if decision == 'pending' and safe_bulk_count and kind in ('', 'safe_match') %}<section class="bulk-box"><div><h2>Safe matches ready for bulk approval · {{safe_bulk_count}}</h2><p>Target parent and full address already match. Ownership-changing fields and manual-review cases are excluded.</p>{% if kind != 'safe_match' %}<p><a href="/?run={{run_id}}&decision=pending&kind=safe_match">Review all {{safe_bulk_count}} Safe Match cards</a></p>{% endif %}</div><form method="post" action="/bulk-approve-safe?run={{run_id}}&kind={{kind}}"><button onclick="return confirm('Approve and write all {{safe_bulk_count}} safe matches to CRM? Each proposal will be recorded separately.')">Approve all {{safe_bulk_count}}</button></form></section>{% endif %}
{% if decision == 'pending' and missing_bulk_count and kind in ('', 'missing_from_website') %}<section class="bulk-box review-bulk"><div><h2>Missing-from-website reviews ready · {{missing_bulk_count}}</h2><p>Approval only sets Status to Needs Review and adds an evidence note. It does not deactivate or re-parent accounts.</p><details><summary>View {{missing_bulk_count}} account{{'' if missing_bulk_count == 1 else 's'}}</summary><ol class="bulk-list">{% for item in missing_bulk_proposals %}<li><a href="/?run={{run_id}}&decision=pending&kind=missing_from_website#proposal-{{item.fingerprint}}">{{item.name}}</a></li>{% endfor %}</ol></details></div><form method="post" action="/bulk-approve-missing?run={{run_id}}&kind={{kind}}"><button onclick="return confirm('Flag all {{missing_bulk_count}} missing-from-website accounts as Needs Review and write their notes to CRM?')">Approve all {{missing_bulk_count}}</button></form></section>{% endif %}
<p class="showing">Showing <b>{{counts.shown}}</b> of {{counts.total}} proposals</p>
{% for p in proposals %}<article id="proposal-{{p.fingerprint}}" class="card {{p.kind}}"><div class="eyebrow"><span class="badge">{{p.kind_label}}</span><span class="badge confidence {{p.confidence_class}}">{{p.confidence_label}}</span></div>
<h2>{{p.display_title}}</h2><p class="match-subtitle">{{p.match_subtitle|safe}}</p><p class="reason">Match evidence · {{p.evidence|join(' · ')}}</p>
{% if p.kind == 'duplicate' %}<div class="cols three">
<section class="panel"><h3>1 · Operator website evidence</h3>{% for label,value,mismatch in p.website_fields %}<div class="field"><span class="label">{{label}}</span><span class="value">{{value}}</span></div>{% endfor %}{% if p.community.source_url %}<p class="source-meta">Source: <a href="{{p.community.source_url}}" target="_blank" rel="noreferrer">facility page</a> · collected {{p.source_collected_display}}</p>{% endif %}</section>
<section class="panel keep"><h3>2 · CRM — keep this account</h3><div class="account-id">{{p.survivor.account_id}}</div>{% for label,value,mismatch in p.survivor_fields %}<div class="field {{'mismatch' if mismatch else ''}}"><span class="label">{{label}}</span><span class="value">{{value}}</span></div>{% endfor %}</section>
<section class="panel remove"><h3>3 · CRM — mark as duplicate</h3><div class="account-id">{{p.account.account_id}}</div>{% for label,value,mismatch in p.crm_fields %}<div class="field {{'mismatch' if mismatch else ''}}"><span class="label">{{label}}</span><span class="value">{{value}}</span></div>{% endfor %}</section></div><p class="compare-note"><b>Highlighted</b> differs from the website after normalization.</p>
{% else %}<div class="cols"><section class="panel"><h3>Operator website evidence</h3>{% if p.community %}{% for label,value,mismatch in p.website_fields %}<div class="field {{'mismatch' if mismatch else ''}}"><span class="label">{{label}}</span><span class="value">{{value}}</span></div>{% endfor %}<p class="source-meta">Source: <a href="{{p.community.source_url}}" target="_blank" rel="noreferrer">facility page</a> · collected {{p.source_collected_display}}</p>{% else %}<span class="label">No corresponding location in this operator website snapshot</span>{% endif %}</section>
<section class="panel"><h3>CRM — before change</h3>{% if p.account %}{% for label,value,mismatch in p.crm_fields %}<div class="field {{'mismatch' if mismatch else ''}}"><span class="label">{{label}}</span><span class="value">{{value}}</span></div>{% endfor %}{% else %}<span class="label">No CRM account exists</span>{% endif %}</section></div>{% endif %}
<section class="changes"><h3>Recommendation</h3><p>{{p.recommendation|safe}}</p><h3>What will happen</h3><ul>{% for change in p.change_descriptions %}<li>{{change|safe}}</li>{% endfor %}</ul></section>
<div class="actions"><span class="decision">{{p.decision}}</span>{% if p.decision == 'pending' %}<form method="post" action="/decide/{{p.fingerprint}}?run={{run_id}}&kind={{kind}}"><button class="approve" name="decision" value="approved" onclick="return confirm('Apply this proposal to the CRM?')">Approve & write</button> <button class="reject" name="decision" value="rejected">Reject</button></form>{% elif p.decision == 'rejected' %}<form method="post" action="/undo/{{p.fingerprint}}?run={{run_id}}&kind={{kind}}"><button class="undo" onclick="return confirm('Return this rejected proposal to Pending?')">Return to pending</button></form>{% elif p.decision == 'approved' %}<form method="post" action="/undo/{{p.fingerprint}}?run={{run_id}}&kind={{kind}}"><button class="undo" onclick="return confirm('Revert the CRM changes from this approval? Created records cannot be deleted and will be marked Inactive.')">Undo &amp; revert CRM</button></form>{% elif p.decision == 'withdrawn' and p.can_retry %}<form method="post" action="/restore/{{p.fingerprint}}?run={{run_id}}&kind={{kind}}"><button class="undo" onclick="return confirm('Return this proposal to Pending so it can be reviewed and approved again?')">Return to pending</button></form>{% endif %}</div>{% if p.decision != 'pending' and p.decision_time %}<p class="audit-time">{{p.decision|title}} at {{p.decision_time}}</p>{% endif %}
</article>{% else %}<p>No proposals match these filters.</p>{% endfor %}</main>
<script>
const returnKey = 'bellhaven-review-return';
document.addEventListener('submit', event => {
  const card = event.target.closest('.card');
  if (!card) return;
  const cards = [...document.querySelectorAll('.card')];
  const index = cards.indexOf(card);
  const nearby = cards[index + 1] || cards[index - 1];
  sessionStorage.setItem(returnKey, JSON.stringify({
    url: location.pathname + location.search,
    anchor: nearby ? nearby.id : '',
    y: window.scrollY
  }));
});
window.addEventListener('DOMContentLoaded', () => {
  const raw = sessionStorage.getItem(returnKey);
  if (!raw) return;
  sessionStorage.removeItem(returnKey);
  const saved = JSON.parse(raw);
  if (saved.url !== location.pathname + location.search) return;
  requestAnimationFrame(() => {
    const target = saved.anchor && document.getElementById(saved.anchor);
    if (target) target.scrollIntoView({block: 'start'});
    else window.scrollTo({top: saved.y || 0});
  });
});
</script></body></html>
"""


LABELS = {"chow": "CHOW", "duplicate": "Duplicate", "create": "New account", "missing_from_website": "Missing from website", "update": "Update"}
FIELD_LABELS = {"name": "Name", "billing_street": "Street", "billing_city": "City", "billing_state": "State", "billing_zip": "ZIP", "care_type": "Care offerings", "phone": "Phone", "parent_id": "Parent account", "status": "Status", "note": "Note", "duplicate_of_account": "Duplicate of", "chow_current_account": "CHOW successor", "is_active": "Active"}


def _display(value):
    if isinstance(value, bool): return "Yes" if value else "No"
    if isinstance(value, list): return ", ".join(value)
    return str(value if value not in (None, "") else "—")


def _format_timestamp(value) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        return parsed.strftime("%b %d, %Y · %H:%M UTC")
    except ValueError:
        return str(value)


def _view(proposal: dict) -> dict:
    p = dict(proposal)
    p["kind_label"] = "Safe match" if _is_safe_match_candidate(p) else LABELS[p["kind"]]
    community, account = p.get("community") or {}, p.get("account") or {}
    context = p.get("source_context") or {}
    context = {
        "target_parent_name": context.get("target_parent_name", TARGET_PARENT_NAME),
        "target_parent_account_name": context.get("target_parent_account_name", TARGET_PARENT_ACCOUNT_NAME),
        "target_parent_account_id": context.get("target_parent_account_id", ""),
        "source_name": context.get("source_name", SOURCE_NAME),
        "source_base_url": context.get("source_base_url", SOURCE_BASE_URL),
        "website_collected_at": context.get("website_collected_at", ""),
        "crm_collected_at": context.get("crm_collected_at", ""),
    }
    p["source_context"] = context
    p["source_collected_display"] = _format_timestamp(context["website_collected_at"])
    p["display_title"] = community.get("name") or account.get("name") or p.get("summary")
    if p["kind"] == "duplicate":
        p["match_subtitle"] = "One website location · two CRM accounts"
    elif p["kind"] == "create":
        p["match_subtitle"] = "No reliable existing CRM account · create a new account"
    elif p["kind"] == "missing_from_website":
        p["match_subtitle"] = f"CRM account <code>{account.get('account_id','')}</code> · no corresponding website location"
    else:
        p["match_subtitle"] = f"Matched to existing CRM account · <code>{account.get('account_id','')}</code>"
    p["decision_time"] = _format_timestamp(p.get("decided_at")) if p.get("decided_at") else ""
    # Re-approving field updates is safe and idempotent. Proposals that create
    # records stay withdrawn because the API cannot delete the first creation;
    # blindly approving those again would create a duplicate account/contact.
    p["can_retry"] = all(op.get("action") in {"update_account", "update_contact", "create_contact"} for op in p.get("operations", []))
    evidence = set(p.get("evidence") or [])
    if "name, care offerings, and phone all differ" in evidence and "different parent" in evidence:
        p["confidence_label"], p["confidence_class"] = "Manual review required", "review"
    elif p["kind"] == "create":
        p["confidence_label"], p["confidence_class"] = "No reliable CRM match", "low"
    elif p["kind"] == "missing_from_website":
        p["confidence_label"], p["confidence_class"] = "Manual review required", "review"
    elif p["kind"] in {"chow", "duplicate"}:
        p["confidence_label"], p["confidence_class"] = "High confidence", "high"
    elif "exact normalized street" in evidence and "same ZIP" in evidence:
        p["confidence_label"], p["confidence_class"] = "High confidence", "high"
    elif "same ZIP" in evidence and "same phone" in evidence:
        p["confidence_label"], p["confidence_class"] = "High confidence", "high"
    elif "exact normalized street" in evidence or "same phone" in evidence:
        p["confidence_label"], p["confidence_class"] = "Medium confidence", "medium"
    else:
        p["confidence_label"], p["confidence_class"] = "Low confidence", "low"
    website_care = " & ".join(community.get("care_offerings") or [])
    def compare(record: dict) -> dict[str, bool]:
        return {
        # Use tolerant names to find the account, but highlight any difference in
        # the official website display name so the CRM can be synchronized.
        "name": bool(record) and clean(community.get("name")) != clean(record.get("name")),
        "address": bool(record) and normalize_street(community.get("street")) != normalize_street(record.get("billing_street")),
        "location": bool(record) and any((
            clean(community.get("city")) != clean(record.get("billing_city")),
            clean(community.get("state")) != clean(record.get("billing_state")),
            normalize_zip(community.get("zip")) != normalize_zip(record.get("billing_zip")),
        )),
        "care": bool(record) and clean(website_care) != clean(record.get("care_type")),
        "parent": bool(record) and record.get("parent_name") != context["target_parent_account_name"],
        "phone": bool(record) and normalize_phone(community.get("phone")) != normalize_phone(record.get("phone")),
        }
    mismatches = compare(account)
    p["website_fields"] = [
        ("Name", _display(community.get("name")), mismatches["name"]),
        ("Address", _display(community.get("street")), mismatches["address"]),
        ("City / State / ZIP", f"{community.get('city','')} {community.get('state','')} {community.get('zip','')}".strip(), mismatches["location"]),
        ("Care offerings", _display(community.get("care_offerings")), mismatches["care"]),
        ("Claimed parent", context["target_parent_name"], mismatches["parent"]),
        ("Administrator", _display(community.get("administrator")), False),
        ("Phone", _display(community.get("phone")), mismatches["phone"]),
        ("Evidence source", context["source_name"], False),
        ("Discovered via", _display(community.get("discovery_method") or "operator directory"), False),
    ]
    p["crm_fields"] = [
        ("Name", _display(account.get("name")), mismatches["name"]),
        ("Address", _display(account.get("billing_street")), mismatches["address"]),
        ("City / State / ZIP", f"{account.get('billing_city','')} {account.get('billing_state','')} {account.get('billing_zip','')}".strip(), mismatches["location"]),
        ("Care type", _display(account.get("care_type")), mismatches["care"]),
        ("Parent", _display(account.get("parent_name")), mismatches["parent"]),
        ("Administrator", _display(account.get("active_administrators") or []), False),
        ("Phone", _display(account.get("phone")), mismatches["phone"]),
        ("Revenue / AR", f"${account.get('lifetime_revenue',0):,.0f} / ${account.get('outstanding_ar',0):,.0f}", False),
        ("Last updated", _format_timestamp(account.get("updated_at")), False),
    ]
    descriptions = []
    survivor = p.get("survivor") or {}
    survivor_mismatches = compare(survivor)
    p["survivor_fields"] = [
        ("Name", _display(survivor.get("name")), survivor_mismatches["name"]),
        ("Address", _display(survivor.get("billing_street")), survivor_mismatches["address"]),
        ("City / State / ZIP", f"{survivor.get('billing_city','')} {survivor.get('billing_state','')} {survivor.get('billing_zip','')}".strip(), survivor_mismatches["location"]),
        ("Care type", _display(survivor.get("care_type")), survivor_mismatches["care"]),
        ("Parent", _display(survivor.get("parent_name")), survivor_mismatches["parent"]),
        ("Phone", _display(survivor.get("phone")), survivor_mismatches["phone"]),
        ("Revenue / AR", f"${survivor.get('lifetime_revenue',0):,.0f} / ${survivor.get('outstanding_ar',0):,.0f}", False),
        ("Last updated", _format_timestamp(survivor.get("updated_at")), False),
    ]
    if p["kind"] == "duplicate":
        p["recommendation"] = "Duplicate review: keep the CRM record that best matches the operator evidence; retain but deactivate the losing record."
        descriptions.append(
            f"<b>Recommendation:</b> keep CRM account <b>{survivor.get('account_id')}</b> because it is the record selected by the website match; "
            f"mark <b>{account.get('account_id')}</b> inactive and link it to the survivor. No record is deleted."
        )
    elif p["kind"] == "missing_from_website":
        p["recommendation"] = "Ownership review: this CRM account is assigned to the target parent but has no corresponding location in the operator website snapshot."
    elif p["kind"] == "create":
        p["recommendation"] = "New account: no reliable CRM match was found for this operator-listed facility."
    elif p["confidence_label"] == "Manual review required":
        p["recommendation"] = "Manual review: the parent differs and identity evidence is not strong enough to safely update or infer a change of ownership."
    elif account and account.get("parent_id") != context["target_parent_account_id"]:
        if account.get("parent_id"):
            p["recommendation"] = "Possible ownership change: additional identity evidence links this location to a differently parented CRM account; apply the CHOW/re-parent SOP."
        else:
            p["recommendation"] = f"Parent assignment: the CRM account has no parent, and the operator evidence supports assigning it to {context['target_parent_name']}."
    else:
        p["recommendation"] = "Stale facility data update: the CRM parent already matches the target parent, and the operator evidence supports synchronizing the differing fields."
    for op in p["operations"]:
        action = op["action"]
        if action == "update_account":
            for field, new in op["fields"].items():
                old = account.get(field, "—")
                # Salesforce relations are written with IDs, but reviewers need
                # the human-readable account name to understand the proposal.
                if field == "parent_id" and new == context["target_parent_account_id"]:
                    old = account.get("parent_name") or "—"
                    new = context["target_parent_account_name"] or context["target_parent_name"]
                descriptions.append(f"<b>{FIELD_LABELS.get(field, field)}</b>: <span class='old'>{_display(old)}</span><span class='arrow'>→</span><span class='new'>{_display(new)}</span>")
        elif action == "create_account": descriptions.append(f"Create a new active account under {context['target_parent_name']} for <b>{op['fields']['name']}</b>.")
        elif action == "chow_create": descriptions.append(f"Preserve the old billing account, create a new {context['target_parent_name']} account, then link the old record to the new one through <b>chow_current_account</b>.")
        elif action == "create_contact": descriptions.append(f"Add <b>{op['fields']['name']}</b> as the active Administrator contact.")
        elif action == "update_contact": descriptions.append("Mark the prior Administrator contact inactive.")
    p["change_descriptions"] = descriptions
    return p


def _is_safe_match_candidate(proposal: dict) -> bool:
    """Classify same-parent, same-location, non-ownership updates."""
    if proposal.get("kind") != "update":
        return False
    account = proposal.get("account") or {}
    context = proposal.get("source_context") or {}
    if not context.get("target_parent_account_id") or account.get("parent_id") != context["target_parent_account_id"]:
        return False
    evidence = set(proposal.get("evidence") or [])
    required = {"exact normalized street", "same ZIP", "same city", "same state"}
    if not required.issubset(evidence) or "different parent" in evidence:
        return False
    allowed_actions = {"update_account", "update_contact", "create_contact"}
    forbidden_account_fields = {"parent_id", "status", "note", "duplicate_of_account", "chow_current_account"}
    for operation in proposal.get("operations") or []:
        if operation.get("action") not in allowed_actions:
            return False
        if operation.get("action") == "update_account" and forbidden_account_fields.intersection(operation.get("fields") or {}):
            return False
    return bool(proposal.get("operations"))


def _is_safe_bulk_match(proposal: dict) -> bool:
    """Only Pending Safe Match proposals are eligible for one-click approval."""
    return proposal.get("decision") == "pending" and _is_safe_match_candidate(proposal)


def _change_type(proposal: dict) -> str:
    return "safe_match" if _is_safe_match_candidate(proposal) else proposal.get("kind", "")


def _is_safe_missing_review(proposal: dict) -> bool:
    """Bulk missing-site approval may only flag Needs Review and add a note."""
    if proposal.get("decision") != "pending" or proposal.get("kind") != "missing_from_website":
        return False
    operations = proposal.get("operations") or []
    if len(operations) != 1 or operations[0].get("action") != "update_account":
        return False
    fields = operations[0].get("fields") or {}
    return set(fields) == {"status", "note"} and fields.get("status") == "Needs Review" and bool(fields.get("note"))


def _bootstrap_runs(store: ReviewStore) -> list[dict]:
    rows = store.list()
    saved = next((p.get("source_context") for p in rows if p.get("source_context")), {})
    legacy = {
        "run_id": "legacy-bellhaven",
        "target_parent_name": saved.get("target_parent_name", TARGET_PARENT_NAME),
        "target_parent_account_name": saved.get("target_parent_account_name", TARGET_PARENT_ACCOUNT_NAME),
        "target_parent_account_id": saved.get("target_parent_account_id", ""),
        "source_name": saved.get("source_name", SOURCE_NAME),
        "source_base_url": saved.get("source_base_url", SOURCE_BASE_URL),
        "website_collected_at": saved.get("website_collected_at", ""),
        "crm_collected_at": saved.get("crm_collected_at", ""),
    }
    store.ensure_legacy_run(legacy)
    return store.list_runs()


def _all_accounts() -> list[dict]:
    try:
        return CRMClient().accounts()
    except Exception:
        snapshot = Path(DATA_DIR / "crm_snapshot.json")
        if not snapshot.exists():
            raise
        return json.loads(snapshot.read_text(encoding="utf-8"))["accounts"]


def _parent_accounts(accounts: list[dict] | None = None) -> list[dict]:
    accounts = accounts or _all_accounts()
    return sorted(
        [a for a in accounts if "Parent Account" in a.get("name", "")],
        key=lambda a: a.get("name", ""),
    )


@app.route("/runs/new", methods=["GET", "POST"])
def new_run():
    store = ReviewStore(DATABASE_PATH)
    runs = _bootstrap_runs(store)
    current_run_id = request.args.get("from") or (runs[0]["run_id"] if runs else "")
    form_accounts = _all_accounts()
    parents = _parent_accounts(form_accounts)
    values = {
        "parent_id": request.form.get("parent_id", parents[0]["account_id"] if parents else ""),
        "source_name": request.form.get("source_name", ""),
        "source_url": request.form.get("source_url", ""),
    }
    error = ""
    preview = []
    if request.method == "POST":
        try:
            source_url = validate_public_https_url(values["source_url"])
            parent = next((p for p in parents if p["account_id"] == values["parent_id"]), None)
            if not parent:
                raise ValueError("Select a valid CRM parent account.")
            if not values["source_name"].strip():
                raise ValueError("Enter a descriptive evidence source name.")
            parent_name = parent["name"].removesuffix(" (Parent Account)")
            preview = scrape_communities(
                base_url=source_url,
                candidate_names=candidate_names_for_parent(form_accounts, parent_name),
            )
            if request.form.get("action") == "run":
                crm = CRMClient()
                accounts, contacts = crm.accounts(), crm.contacts()
                # Re-validate against the fresh CRM snapshot used for matching.
                parent = next((a for a in accounts if a.get("account_id") == values["parent_id"] and "Parent Account" in a.get("name", "")), None)
                if not parent:
                    raise ValueError("The selected parent no longer exists in the fresh CRM snapshot.")
                now = datetime.now(timezone.utc).isoformat()
                run_id = uuid.uuid4().hex[:12]
                parent_name = parent["name"].removesuffix(" (Parent Account)")
                context = {
                    "run_id": run_id,
                    "target_parent_name": parent_name,
                    "target_parent_account_name": parent["name"],
                    "target_parent_account_id": parent["account_id"],
                    "source_name": values["source_name"].strip(),
                    "source_base_url": source_url,
                    "website_collected_at": now,
                    "crm_collected_at": datetime.now(timezone.utc).isoformat(),
                    "created_at": now,
                }
                proposals = build_proposals(preview, accounts, contacts, context)
                store.create_run(context)
                store.sync(proposals, run_id)
                run_dir = Path(DATA_DIR / "runs" / run_id)
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "communities.json").write_text(json.dumps([c.to_dict() for c in preview], indent=2), encoding="utf-8")
                (run_dir / "crm_snapshot.json").write_text(json.dumps({"accounts": accounts, "contacts": contacts}, indent=2), encoding="utf-8")
                (run_dir / "proposals.json").write_text(json.dumps(proposals, indent=2), encoding="utf-8")
                return redirect(url_for("index", run=run_id, decision="pending"))
        except Exception as exc:
            error = str(exc)
            preview = []
    return render_template_string(NEW_RUN_PAGE, parents=parents, values=values, preview=preview,
                                  error=error, current_run_id=current_run_id)


@app.get("/")
def index():
    decision = request.args.get("decision", "pending")
    kind = request.args.get("kind", "")
    store = ReviewStore(DATABASE_PATH)
    runs = _bootstrap_runs(store)
    requested_run = request.args.get("run", "")
    run_id = requested_run if any(r["run_id"] == requested_run for r in runs) else (runs[0]["run_id"] if runs else "")
    all_records = [p for p in store.list() if p.get("run_id") == run_id]
    safe_bulk_records = [p for p in all_records if _is_safe_bulk_match(p)]
    safe_bulk_count = len(safe_bulk_records)
    safe_bulk_proposals = [{
        "fingerprint": p["fingerprint"],
        "name": (p.get("community") or {}).get("name") or (p.get("account") or {}).get("name") or p.get("summary"),
    } for p in safe_bulk_records]
    missing_bulk_records = [p for p in all_records if _is_safe_missing_review(p)]
    missing_bulk_count = len(missing_bulk_records)
    missing_bulk_proposals = [{
        "fingerprint": p["fingerprint"],
        "name": (p.get("account") or {}).get("name") or p.get("summary"),
    } for p in missing_bulk_records]
    status_counts = Counter(p["decision"] for p in all_records)
    status_filtered = all_records if decision == "all" else [p for p in all_records if p["decision"] == decision]
    type_counts = Counter(_change_type(p) for p in status_filtered)
    type_counts["total"] = len(status_filtered)
    all_proposals = [p for p in status_filtered if not kind or _change_type(p) == kind]
    priority = {"chow": 0, "duplicate": 1, "create": 2, "missing_from_website": 3, "update": 4}
    all_proposals.sort(key=lambda p: (priority.get(p["kind"], 9), -p["confidence"], p["summary"]))
    counts = {"total": len(all_records), "shown": len(all_proposals), "pending": status_counts["pending"],
              "approved": status_counts["approved"], "rejected": status_counts["rejected"],
              "withdrawn": status_counts["withdrawn"]}
    saved_context = store.get_run(run_id) if run_id else {}
    context = {
        "target_parent_name": saved_context.get("target_parent_name", TARGET_PARENT_NAME),
        "target_parent_account_id": saved_context.get("target_parent_account_id", ""),
        "source_name": saved_context.get("source_name", SOURCE_NAME),
        "source_base_url": saved_context.get("source_base_url", SOURCE_BASE_URL),
        "website_collected_display": _format_timestamp(saved_context.get("website_collected_at")),
        "crm_collected_display": _format_timestamp(saved_context.get("crm_collected_at")),
    }
    return render_template_string(PAGE, proposals=[_view(p) for p in all_proposals], counts=counts,
                                  type_counts=type_counts, labels=LABELS, decision=decision, kind=kind,
                                  context=context, safe_bulk_count=safe_bulk_count,
                                  safe_bulk_proposals=safe_bulk_proposals,
                                  missing_bulk_count=missing_bulk_count,
                                  missing_bulk_proposals=missing_bulk_proposals,
                                  bulk_ok=request.args.get("bulk_ok", type=int),
                                  bulk_failed=request.args.get("bulk_failed", type=int), runs=runs, run_id=run_id)


@app.post("/bulk-approve-safe")
def bulk_approve_safe():
    store = ReviewStore(DATABASE_PATH)
    run_id = request.args.get("run", "")
    proposals = [p for p in store.list("pending") if p.get("run_id") == run_id and _is_safe_bulk_match(p)]
    crm = CRMClient()
    approved = failed = 0
    for proposal in proposals:
        try:
            result = execute(proposal, crm)
            store.decide(proposal["fingerprint"], "approved", result)
            approved += 1
        except Exception:
            # A failed proposal remains Pending and can be reviewed/retried
            # individually; successfully completed proposals retain their own
            # audit records.
            failed += 1
    return redirect(url_for("index", run=run_id, decision="pending", kind=request.args.get("kind", ""),
                            bulk_ok=approved, bulk_failed=failed))


@app.post("/bulk-approve-missing")
def bulk_approve_missing():
    store = ReviewStore(DATABASE_PATH)
    run_id = request.args.get("run", "")
    proposals = [p for p in store.list("pending") if p.get("run_id") == run_id and _is_safe_missing_review(p)]
    crm = CRMClient()
    approved = failed = 0
    for proposal in proposals:
        try:
            result = execute(proposal, crm)
            store.decide(proposal["fingerprint"], "approved", result)
            approved += 1
        except Exception:
            failed += 1
    return redirect(url_for("index", run=run_id, decision="pending", kind=request.args.get("kind", ""),
                            bulk_ok=approved, bulk_failed=failed))


@app.post("/decide/<fingerprint>")
def decide(fingerprint: str):
    store = ReviewStore(DATABASE_PATH)
    try: proposal = store.get(fingerprint)
    except KeyError: abort(404)
    if proposal["decision"] != "pending": abort(409)
    decision = request.form.get("decision")
    if decision == "rejected": store.decide(fingerprint, "rejected", {"message": "Rejected by reviewer"})
    elif decision == "approved":
        result = execute(proposal, CRMClient())
        store.decide(fingerprint, "approved", result)
    else: abort(400)
    return redirect(url_for("index", run=proposal.get("run_id", ""), decision="pending", kind=request.args.get("kind", "")))


@app.post("/undo/<fingerprint>")
def undo(fingerprint: str):
    store = ReviewStore(DATABASE_PATH)
    try: proposal = store.get(fingerprint)
    except KeyError: abort(404)
    if proposal["decision"] == "rejected":
        if not store.transition(fingerprint, "rejected", "pending", {"message": "Rejection withdrawn"}): abort(409)
        return redirect(url_for("index", run=proposal.get("run_id", ""), decision="pending", kind=request.args.get("kind", "")))
    if proposal["decision"] == "approved":
        rollback_result = rollback(proposal, CRMClient())
        audit = {"approval_result": proposal.get("result"), "rollback_result": rollback_result}
        if not store.transition(fingerprint, "approved", "withdrawn", audit): abort(409)
        return redirect(url_for("index", run=proposal.get("run_id", ""), decision="approved", kind=request.args.get("kind", "")))
    abort(409)


@app.post("/restore/<fingerprint>")
def restore(fingerprint: str):
    store = ReviewStore(DATABASE_PATH)
    try: proposal = store.get(fingerprint)
    except KeyError: abort(404)
    retryable = all(op.get("action") in {"update_account", "update_contact", "create_contact"} for op in proposal.get("operations", []))
    if proposal["decision"] != "withdrawn" or not retryable: abort(409)
    if not store.reopen(fingerprint): abort(409)
    return redirect(url_for("index", run=proposal.get("run_id", ""), decision="withdrawn", kind=request.args.get("kind", "")))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
