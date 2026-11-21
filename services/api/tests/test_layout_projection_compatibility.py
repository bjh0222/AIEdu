"""Historical display projections remain valid; structural edits remain detectable."""
from copy import deepcopy

import pytest

from test_v2_inherited_format_runtime import candidates


def fixture_unit():
    from app.v2.format_runtime import EXERCISE_LAYOUT
    from app.v2.items import normalize_items
    value = candidates()
    for item in value['items']:
        item['answer']['final'] = '答案：' + item['answer']['final']
        if item['question_type'] == '综合题':
            item['answer']['display_reasoning'] = '解析：' + item['answer']['display_reasoning']
    items = normalize_items(value, 'exercise', 'u', EXERCISE_LAYOUT, ['习题2.1'])
    return {'unit_id': 'u', 'title': '合成章节', 'artifact_role': 'exercise',
            'artifact_roles': ['exercise', 'answer'], 'format_contract': EXERCISE_LAYOUT, 'items': items}


@pytest.mark.parametrize('version', [1, 2, 3])
def test_exact_historical_layout_keeps_original_bytes_and_rejects_edits(version):
    from app.v2.format_runtime import check_layout
    from app.v2.items import render_unit_items
    unit = fixture_unit()
    unit['body'] = render_unit_items(unit['items'], unit, projection_version=version)
    original = deepcopy(unit)
    assert check_layout(unit) and unit == original
    assert ('答案：答案：' in unit['body']) == (version < 3)
    assert ('解析：解析：' in unit['body']) == (version < 3)
    assert ('- [ ]\n' in unit['body']) == (version == 1)
    for old, new in [('**1.**', '**99.**'), ('### 参考答案', '### 答案被删改'),
                     ('合成判断题。', 'different question'), ('正确', 'incorrect answer')]:
        changed = {**unit, 'body': unit['body'].replace(old, new, 1)}
        assert changed['body'] != unit['body'] and not check_layout(changed)
    assert not check_layout({**unit, 'body': unit['body'] + '\nextra paragraph'})
    assert check_layout({**unit, 'layout_projection_version': version})
    assert not check_layout({**unit, 'layout_projection_version': 999})
    assert not check_layout({**unit, 'layout_projection_version': True})
    if version < 3:
        assert not check_layout({**unit, 'layout_projection_version': 3})


def test_new_projection_and_export_keep_clean_labels_and_coupled_roles():
    from app.v2.items import project_units, render_unit_items
    unit = fixture_unit()
    body = render_unit_items(unit['items'], unit)
    assert '- [ ] 待判断' in body
    assert '答案：答案：' not in body and '解析：解析：' not in body
    projected = list(project_units([unit]))
    assert {u['artifact_role'] for u in projected} == {'exercise', 'answer'}
    assert all('### 参考答案' in u['body'] for u in projected)


def test_review_adapter_is_bound_to_exact_source_and_preserves_original_spec():
    from app.v2.contracts import sha
    from app.v2.format_runtime import presentation_contract
    from app.v2.items import render_unit_items
    unit = fixture_unit()
    unit['body'] = render_unit_items(unit['items'], unit, projection_version=1)
    unit['content_hash'] = sha(unit['body'])
    bindings = [{'artifact_role': 'exercise', 'snapshot_hash': sha('original'),
                 'snapshot': {'checkbox': '<input type="checkbox">'}}]
    original = deepcopy((unit, bindings))
    policy = presentation_contract(unit, bindings)
    assert policy['matching_projection_versions'] == [1]
    assert policy['source_hash'] == unit['content_hash']
    assert policy['source_spec_snapshot_hashes'] == [bindings[0]['snapshot_hash']]
    assert policy['learner_projection_hash'] != unit['content_hash']
    assert 'GFM' in policy['rules']['checkbox'] and 'premises' in policy['rules']['semantic_scope']
    assert policy['professional_certification'] is False and (unit, bindings) == original
    assert presentation_contract({**unit, 'content_hash': 'wrong'}, bindings) is None
    changed = unit['body'] + 'extra content'
    assert presentation_contract({**unit, 'body': changed, 'content_hash': sha(changed)}, bindings) is None
    assert presentation_contract({**unit, 'format_contract': 'other'}, bindings) is None


def test_effective_presentation_reaches_both_checks_but_not_blind_or_item_requests(client, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from test_v2_optimization import heading_task
    from app.db import SessionLocal
    from app.v2 import checker_batch
    from app.v2.contracts import sha
    from app.v2.items import render_unit_items
    from app.v2.quality import check_snapshot
    from app.v2.runtime import job, load
    _, snapshot = heading_task(client)
    with SessionLocal() as db:
        state = load(job(db, snapshot['run_id']).state_json)
    unit = fixture_unit()
    unit['body'] = render_unit_items(unit['items'], unit, projection_version=1)
    unit['content_hash'] = sha(unit['body'])
    state['context']['domain_key'] = 'math_logic'
    state['plan']['units'] = [
        {'unit_id': 'u', 'title': '合成章节', 'artifact_role': 'exercise', 'source_sections': ['local section']},
        {'unit_id': 'other', 'title': 'Other chapter', 'artifact_role': 'exercise', 'source_sections': ['other section']},
    ]
    original_task = deepcopy(state['context'])
    requests = []

    def invoke(*args, **kwargs):
        requests.append(kwargs)
        data = kwargs['data']
        if kwargs['stage'] in {'checking', 'checking_perturbation'}:
            assert data['presentation_contract']['source_hash'] == unit['content_hash']
            assert 'Wrapper-owned presentation_contract' in kwargs['system']
            assert data['unit']['body'] == unit['body']
            assert kwargs['output_tokens'] == 32768
            assert data['review_scope']['scope'] == 'single_planned_unit'
            assert data['review_scope']['required_task_unit_count'] == 2
            assert data['review_scope']['local_source_sections'] == ['local section']
            assert len(data['review_scope']['planned_units']) == 2
            assert 'do not require every task chapter' in kwargs['system']
            value = {'checks': [{'item_id': c['item_id'], 'result': 'undecidable', 'findings': []} for c in data['checks']]}
        else:
            assert 'presentation_contract' not in data
            assert 'review_scope' not in data
            value = {'solutions': []} if kwargs['stage'] == 'blind_solving' else {'item_reviews': []}
        return value, {'call_id': str(uuid4()), 'model_family': 'deepseek'}

    profile = SimpleNamespace(llm_model_family='deepseek', llm_model='fixture', llm_base_url='https://fixture.invalid')
    monkeypatch.setattr(checker_batch, 'invoke', invoke)
    monkeypatch.setattr(checker_batch, 'for_role', lambda *a, **k: profile)
    monkeypatch.setattr('app.v2.capacity.get_capacity', lambda *a: {'max_output_tokens': 128000})
    checker_batch.evaluate_batch(snapshot['run_id'], 'fixture', 0, state, [unit], 'version', None,
        check_snapshot(state, [unit], 'version'), profile, profile, False)
    assert {r['stage'] for r in requests} == {'blind_solving', 'item_reviewing', 'checking', 'checking_perturbation'}
    assert state['context'] == original_task
