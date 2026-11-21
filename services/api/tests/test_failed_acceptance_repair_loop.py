"""Production quality entry: failed acceptance must schedule bounded protected repair."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models import LearningGoal
from app.services import exam_check as ec, exam_verify as ev, proposition_generated as pg
from app.services import proposition_knowledge as pk, quality_findings as qf, tutorials
from app.services.generation_acceptance import adapt_task_context

BODY = '''> 本卷满分 100 分，共 1 题。

### 解答题

**1.**（100 分）求题设结论，并给出完整推理。

### 参考答案

**1.** 第一步由已知条件推出中间结论，再详细说明推理的充分性和必要性，最后得到错误结论。
'''


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_failed_then_followup_repair_or_honest_limit(client, monkeypatch, repair_succeeds):
    features = []

    class AI:
        is_demo = False
        def __init__(self, **kwargs):
            pass
        def _real_json(self, feature, *args):
            features.append(feature)
            return {'defects': [], 'uncertainties': []}
        def _real_text(self, feature, system, payload, goal):
            import json
            features.append(feature)
            original = json.loads(payload)['original']
            return original.replace('错误结论', '正确结论') if repair_succeeds else ''

    def verify(**kwargs):
        wrong = '错误结论' in kwargs['markdown']
        return {'title': kwargs['title'], 'verified': 1, 'applied': [], 'conflicts': [],
                'defects': [{'number': 1, 'defect': '第1题参考答案错误', 'reasoning': '独立仲裁确认错误'}] if wrong else [],
                'skipped': [], 'changed': False}

    monkeypatch.setattr(pg, 'AIService', AI)
    monkeypatch.setattr(ev, '_verify_one_paper', verify)
    with SessionLocal() as db:
        goal = LearningGoal(title='线代试卷', domain_key='math_logic')
        db.add(goal)
        db.commit()
        pk.save_config(db, {'auto_evaluate_generated': True})
        content = {'format_spec': {'scene': 'exam_paper'}, 'sections': [
            {'id': 's1', 'title': '试卷', 'content': [{'type': 'text', 'body': BODY}]}]}
        quality = {'checks': [], 'issues': []}
        tutorials._apply_exam_quality(db, content, quality, goal, 't1', '', verify_enabled=True, version=2)
    loop = quality['repair_loop']
    assert loop['rounds'][0]['status'] == 'failed'
    assert 'proposition_generated_repair' in features
    if repair_succeeds:
        assert loop['stop_reason'] == 'passed'
        assert quality['passed'] is True
        assert len(loop['rounds']) == 2
    else:
        assert loop['stop_reason'] == 'repair_limit'
        assert quality['status'] == 'failed'
        assert len(loop['rounds']) == tutorials.settings.quality_max_repairs + 1
        assert features.count('proposition_generated_repair') == tutorials.settings.quality_max_repairs


def test_skipped_subjective_confirmed_error_enters_defects(monkeypatch):
    monkeypatch.setattr(ev, '_tool_verify_paper', lambda *args: ([], set()))
    class AI:
        is_demo = False
        def verify_questions(self, *args):
            return [{'number': 1, 'solved': True, 'confidence': 'high',
                     'my_answer_text': '正确结论', 'my_answer': '正确结论', 'reasoning': '完整独立推理'}]
        def arbitrate_answers(self, *args):
            return [{'number': 1, 'verdict': 'paper_wrong', 'confidence': 'high',
                     'correct_answer': '正确结论', 'reasoning': '复算确认原答案错误'}]
    result = ev.verify_paper_with_ai(AI(), SimpleNamespace(domain_key='math_logic'), None, '试卷', BODY, 'exam')
    assert not result['changed']
    assert result['skipped'][0]['verdict'] == 'paper_wrong'
    assert result['defects'][0]['number'] == 1


def test_explicit_confirmation_is_local_to_question():
    result = pg.classify_recheck({'defects': [], 'uncertainties': [
        '第8题已修正，推导成立。', '无法确认第4题设问是否唯一。']},
        [{'number': 8, 'reason': '第8题错误', 'evidence': '推导有误'}])
    assert result['status'] == 'undecidable'
    assert result['findings'][0]['status'] == 'resolved'


def test_second_paper_final_hash_and_unverified_edit():
    ledger = qf.FindingLedger(version=2)
    paper = ec.parse_paper(BODY)
    finding = qf.finding_from_review({'number': 1, 'reason': '答案错误', 'evidence': '推导有误'},
        unit_id='s2:0', paper=paper, finding_id='f1')
    ledger.add_finding(finding)
    new = BODY.replace('错误结论', '正确结论')
    ledger.register_revision(actor='review_repair', before_text=BODY, after_text=new,
                             changed_locations=['answers/1'], finding_ids=['f1'])
    sections = [{'id': id_, 'content': [{'body': text, 'type': 'text'}]} for id_, text in [('s1', BODY), ('s2', new)]]
    result = ledger.finalize({'sections': sections})
    assert result['items'][0]['status'] == 'undecidable'
    assert result['items'][0]['content_hash'] == qf.content_hash(ec.parse_paper(new).answers[1])


def test_acceptance_context_binds_current_version():
    goal = SimpleNamespace(id='g', domain_key='math_logic', hard_constraints='')
    original = adapt_task_context(goal, {}, tutorial_id='t', version=1)
    snapshot = deepcopy(original)
    updated = adapt_task_context(goal, {}, tutorial_id='t', version=2, task_context=original)
    assert original == snapshot
    assert updated['identity']['version'] == 2


def test_api_preserves_repair_history():
    from app.schemas import QualityReport
    loop = {'stop_reason': 'repair_limit', 'rounds': [{'round': 1, 'status': 'failed'}]}
    assert QualityReport(passed=False, repair_loop=loop).model_dump()['repair_loop'] == loop


@pytest.mark.parametrize('structure_checked', [True, False])
def test_unknown_model_review_does_not_erase_completed_structure(structure_checked):
    from app.services.generation_acceptance import quality_acceptance
    goal = SimpleNamespace(id='g', title='试卷', domain_key='math_logic', hard_constraints='')
    content = {'format_spec': {'scene': 'exam_paper'}, 'sections': [
        {'id': 's', 'title': '试卷', 'content': [{'body': BODY}]}]}
    quality = {
        'checks': [{'name': '题号与分值', 'passed': True}] if structure_checked else [],
        'issues': [],
        'proposition_review': {'enabled': True, 'demo': False, 'reports': [
            {'id': 's:0', 'status': 'ok', 'repair': 'not_applied', 'defects': [],
             'open_uncertainties': ['无法确认第1题设问是否唯一。']}]},
    }
    result = quality_acceptance(goal, content, quality, tutorial_id='t', version=2)
    checks = {c['id']: c for c in result['payload']['checks']}
    assert checks['structure']['status'] == ('satisfied' if structure_checked else 'error')
    assert checks['correctness']['status'] == 'undecidable'
    assert result['payload']['status'] == 'needs_review'


def test_running_stage_publishes_running_job(client):
    from app.models import GenerationJob
    from app.services.generation_runtime import GenerationRunContext
    with SessionLocal() as db:
        goal = LearningGoal(title='状态回归', domain_key='math_logic')
        db.add(goal)
        db.flush()
        job = GenerationJob(goal_id=goal.id, job_type='tutorial_sections', status='queued')
        db.add(job)
        db.commit()
        tutorials._stage_job(db, job.id, GenerationRunContext(goal_id=goal.id), 'generating', 'running')
        db.commit()
        assert client.get('/api/generation-jobs/' + job.id).json()['status'] == 'running'
