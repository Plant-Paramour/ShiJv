"""Promote a pair only after its real generation was accepted by the API."""
import json
import sys
import time
from pathlib import Path

from apps.api.launcher_memory import LauncherMemory, environment_stamp
from apps.launcher.model_catalog import fingerprint, report_path, verified_report


class GenerationVerification:
    def __init__(self, directory, model, application_python, inference_python=None):
        self.directory, self.model = Path(directory), str(model)
        self.app, self.inference = application_python, inference_python or sys.executable
        self.model_stamp = fingerprint(model, self.inference)
        self.app_stamp = environment_stamp(self.app)
        self.inference_stamp = environment_stamp(self.inference)

    def record(self, job, result):
        if job.get('kind') not in {'generate', 'partial_generate', 'rewrite'} or result.get('status') != 'succeeded':
            return False
        texts = [candidate.get('content') or candidate.get('full_text') for candidate in result.get('candidates', [])]
        if not texts or not all(isinstance(text, str) and text.strip() for text in texts):
            return False
        if (fingerprint(self.model, self.inference) != self.model_stamp or environment_stamp(self.app) != self.app_stamp
                or environment_stamp(self.inference) != self.inference_stamp):
            return False
        report = dict(ok=True, source='successful_generation', model=self.model, python=self.inference,
                      job_id=job['job_id'], verified_at=time.time(), checks=[dict(id='actual_generation',
                      name='网页实际生成', state='passed', detail='已成功生成非空正文，且任务完成结果已由软件接收')])
        previous = verified_report(self.directory, self.model, self.inference)
        if not (previous and previous.get('ok')):
            path = report_path(self.directory, self.model, self.inference)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        memory = LauncherMemory(self.directory)
        memory.remember(self.app, self.inference, True)
        memory.pairs()
        return True
