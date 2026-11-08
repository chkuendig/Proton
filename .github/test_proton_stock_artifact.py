#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
import hashlib
import io
import json
import tempfile
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('artifact',Path(__file__).with_name('proton-stock-artifact.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def env(mode='cold'):
    return dict(STOCK_MODE=mode,STOCK_SEED_RUN='',STOCK_SEED_ARTIFACT='',STOCK_SEED_DIGEST='',STOCK_SEED_HEAD='',
                GITHUB_REPOSITORY='chkuendig/Proton',GITHUB_REF=m.BRANCH,GITHUB_EVENT_NAME='workflow_dispatch',
                GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_ID='200')


class Inputs(unittest.TestCase):
    def test_cold(self): self.assertEqual(m.validate(env()),'cold')
    def test_off(self): self.assertEqual(m.validate(env('off')),'off')
    def test_warm(self):
        e=env('warm');e.update(STOCK_SEED_RUN='100',STOCK_SEED_ARTIFACT='101',STOCK_SEED_DIGEST='a'*64,STOCK_SEED_HEAD='f'*40)
        self.assertEqual(m.validate(e),'warm')
    def test_bad_inputs(self):
        for key,value in [('GITHUB_REF','refs/heads/main'),('GITHUB_EVENT_NAME','push'),
                          ('GITHUB_RUN_ATTEMPT','2'),('GITHUB_REPOSITORY','foreign/Proton'),
                          ('STOCK_SEED_RUN','1')]:
            with self.subTest(key=key):
                e=env();e[key]=value
                with self.assertRaises(ValueError):m.validate(e)
    def test_missing_seed(self):
        with self.assertRaises(ValueError):m.validate(env('warm'))
    def test_self_seed(self):
        e=env('warm');e.update(STOCK_SEED_RUN='200',STOCK_SEED_ARTIFACT='101',STOCK_SEED_DIGEST='a'*64,STOCK_SEED_HEAD='f'*40)
        with self.assertRaises(ValueError):m.validate(e)


class Fetch(unittest.TestCase):
    def fixture(self, mutation=None, extra=False):
        e=env('warm');e.update(STOCK_SEED_RUN='100',STOCK_SEED_ARTIFACT='101',GITHUB_SHA='f'*40,STOCK_SEED_HEAD='f'*40)
        raw=b'stock archive';ident={'digest':'input'}
        data={'stock.tar.gz':raw,'COLD-QUALIFICATION.json':b'{}',
              'STOCK-INPUT-BEFORE.json':json.dumps(ident).encode(),
              'STOCK-INPUT-AFTER.json':json.dumps(ident).encode(),
              'STOCK-EXPORT.json':json.dumps({'identity':ident,'archive_sha256':hashlib.sha256(raw).hexdigest()}).encode()}
        if extra:data['../escape']=b'bad'
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            for n,value in data.items():z.writestr(n,value)
        payload=buffer.getvalue();e['STOCK_SEED_DIGEST']=hashlib.sha256(payload).hexdigest()
        run={'head_sha':e['GITHUB_SHA'],'head_branch':m.BRANCH.removeprefix('refs/heads/'),
             'status':'completed','conclusion':'success','run_attempt':1,'event':'workflow_dispatch',
             'path':'.github/workflows/zluda.yml'}
        artifact={'name':'proton-stock-seed-100-1','expired':False,'workflow_run':{'id':100,'head_sha':e['GITHUB_SHA']},
                  'digest':'sha256:'+e['STOCK_SEED_DIGEST'],'size_in_bytes':len(payload)}
        if mutation:mutation(run,artifact)
        return e,run,artifact,payload

    def fetch(self, fixture):
        e,run,artifact,payload=fixture
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'seed'
            def download(*args,**kwargs):kwargs['stdout'].write(payload)
            with patch.object(m,'api',side_effect=lambda path:run if '/runs/' in path else artifact),patch.object(
                    m.subprocess,'run',side_effect=download),patch.object(m.shutil,'disk_usage',return_value=SimpleNamespace(free=20*1024**3)):
                m.fetch(e,output)
            self.assertEqual({p.name for p in output.iterdir()},m.FILES)
            self.assertTrue(json.loads((Path(tmp)/'STOCK-SEED-VERIFIED.json').read_text())['PASS'])
            self.assertFalse((Path(tmp)/'seed.zip').exists())

    def test_verified_exact_artifact(self):self.fetch(self.fixture())
    def test_reject_wrong_head(self):
        with self.assertRaisesRegex(ValueError,'run binding'):self.fetch(self.fixture(lambda r,a:r.update(head_sha='0'*40)))
    def test_reject_failed_run(self):
        with self.assertRaisesRegex(ValueError,'run binding'):self.fetch(self.fixture(lambda r,a:r.update(conclusion='failure')))
    def test_reject_expired(self):
        with self.assertRaisesRegex(ValueError,'artifact binding'):self.fetch(self.fixture(lambda r,a:a.update(expired=True)))
    def test_reject_wrong_run_artifact(self):
        with self.assertRaisesRegex(ValueError,'artifact binding'):self.fetch(self.fixture(lambda r,a:a['workflow_run'].update(id=99)))
    def test_reject_wrong_digest(self):
        with self.assertRaisesRegex(ValueError,'artifact binding'):self.fetch(self.fixture(lambda r,a:a.update(digest='sha256:'+'0'*64)))
    def test_reject_zip_traversal_even_with_matching_digest(self):
        with self.assertRaisesRegex(ValueError,'ZIP coverage'):self.fetch(self.fixture(extra=True))
    def test_seed_off(self):
        e=env('off');e['STOCK_SEED_RUN']='1'
        with self.assertRaises(ValueError):m.validate(e)


if __name__=='__main__':unittest.main()
