import unittest
from flask import url_for
from main import app, db  # adjust to your actual entry point
from flask.testing import FlaskClient

class FlaskAppTests(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.client: FlaskClient = self.app.test_client()

    def test_home(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_login_page(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)

    def test_cart(self):
        response = self.client.get('/cart')
        self.assertIn(response.status_code, [200, 302])

    def test_item_page(self):
        # assumes item with ID 1 might exist
        response = self.client.get('/item/1')
        self.assertIn(response.status_code, [200, 404])

    def test_book_route(self):
        response = self.client.post('/book', data={}, follow_redirects=True)
        self.assertIn(response.status_code, [200, 302, 400])

    def test_return_post(self):
        # assumes booking ID 1 might exist or will 404
        response = self.client.post('/return/1', follow_redirects=True)
        self.assertIn(response.status_code, [200, 403, 404])

    def test_admin_dashboard_redirect(self):
        response = self.client.get('/dashboard', follow_redirects=True)
        self.assertIn(response.status_code, [200, 302])

    def test_set_borrower_post(self):
        response = self.client.post('/set-borrower', json={
            "name": "Test",
            "contact": "test@example.com",
            "phone": "123456"
        })
        self.assertIn(response.status_code, [200, 400])

if __name__ == '__main__':
    unittest.main()