import json
from pathlib import Path

def test_review_manifest_is_single_source_and_four_conditions():
    p=Path('intersuit/harness/artifacts/audio_generation_review_ave_reserve_v1/manifest.jsonl')
    rows=[json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    assert {r['source_dataset'] for r in rows} == {'AVE_HF_EXPANDED'}
    assert {r['split'] for r in rows} == {'reserve'}
    groups={}
    for r in rows: groups.setdefault(r['pair_group_id'],set()).add(r['condition'])
    assert len(groups)==20 and all(v=={'correct','mismatch','video_only','silence'} for v in groups.values())

def test_generation_output_audit_normalizes_answer(tmp_path):
    manifest=tmp_path/'m.jsonl'; outputs=tmp_path/'o.jsonl'; report=tmp_path/'r.json'
    base={'pair_group_id':'g','condition':'correct','answer':'Clock'}
    manifest.write_text('\n'.join(json.dumps({**base,'condition':c}) for c in ('correct','mismatch','video_only','silence'))+'\n')
    outputs.write_text('\n'.join(json.dumps({'pair_group_id':'g','condition':c,'prediction':'The clock is audible.','new_token_count':4}) for c in ('correct','mismatch','video_only','silence'))+'\n')
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('audit',Path('intersuit/scripts/audit_audio_generation_review_outputs.py')); mod=module_from_spec(spec); spec.loader.exec_module(mod)
    import sys; old=sys.argv; sys.argv=['audit','--manifest',str(manifest),'--outputs',str(outputs),'--output',str(report)]
    try: mod.main()
    finally: sys.argv=old
    result=json.loads(report.read_text()); assert result['status']=='PASS_AUDIO_GENERATION_OUTPUT_AUDIT'

def test_generation_runner_resolves_repo_relative_paths():
    from importlib.util import spec_from_file_location,module_from_spec
    script=Path('intersuit/scripts/run_audio_generation_review_ave.py').resolve()
    spec=spec_from_file_location('runner',script); mod=module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.repo_path('intersuit/configs/audio_generation_review_ave_reserve_generation_v1.json') == Path('intersuit/configs/audio_generation_review_ave_reserve_generation_v1.json').resolve()
