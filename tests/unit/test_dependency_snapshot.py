import json
from pathlib import Path
import tempfile
import unittest

from tools.dependency_snapshot import ROOT,pins,snapshot


class DependencySnapshotTests(unittest.TestCase):
    def test_all_real_profiles_report_exact_current_pins_without_retired_packages(self):
        report=snapshot(ROOT,'a'*40,'test')
        self.assertEqual(len(report['manifests']),len(list((ROOT/'requirements').glob('*.txt'))))
        for name,manifest in report['manifests'].items():
            self.assertEqual(manifest['file']['source_location'],name)
            self.assertNotIn('chromadb',manifest['resolved'])
            self.assertNotIn('langchain-chroma',manifest['resolved'])
            if 'setuptools' in manifest['resolved']:
                self.assertEqual(manifest['resolved']['setuptools']['package_url'],'pkg:pypi/setuptools@84.1.0')
        self.assertFalse(report['metadata']['transitive_resolution'])
        self.assertIn('%2Bcu130',report['manifests']['requirements/torch-cuda.txt']['resolved']['torch']['package_url'])

    def test_includes_are_recursive_and_conflicts_or_unpinned_dependencies_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'base.txt';profile=root/'profile.txt'
            base.write_text('example==1.2\n',encoding='utf-8')
            profile.write_text('-r base.txt\nother==2.0\n',encoding='utf-8')
            self.assertEqual(pins(profile,root),{'example':'1.2','other':'2.0'})
            for content in ('-r base.txt\nexample==2.0\n','unbounded>=1\n','-r ../outside.txt\n','-r profile.txt\n'):
                profile.write_text(content,encoding='utf-8')
                with self.assertRaises(ValueError):pins(profile,root)
