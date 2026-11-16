import unittest
from scripts.scan_personal_release_secrets import rules


class SecretScanTests(unittest.TestCase):
    def test_finds_high_confidence_and_does_not_echo(self):
        sample = b'ghp_' + b'A' * 36
        self.assertEqual(rules(sample), {'github_token'})
        self.assertEqual(rules(b'postgresql://' + b'person:somepassword@host/db'), {'credential_dsn'})
        self.assertEqual(rules(b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'), {'private_key'})
        self.assertEqual(rules(b'postgresql://user:password@localhost/db'), set())
        self.assertEqual(rules(b'sk-example-placeholder-value-123456'), set())
