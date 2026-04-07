from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient


class AlertApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='Password123!')
        self.client = APIClient()
        resp = self.client.post('/api/v1/auth/login', {'username': 'alice', 'password': 'Password123!'}, format='json')
        self.assertEqual(resp.status_code, 200)
        token = resp.data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')

    def test_alert_list(self):
        resp = self.client.get('/api/v1/alerts/list/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('alerts', resp.data)
