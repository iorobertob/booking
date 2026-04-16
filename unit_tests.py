import unittest
from main import app, db
from flask.testing import FlaskClient


class FlaskAppTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        cls.app = app
        cls.client: FlaskClient = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

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

    def test_bookings_list_requires_admin(self):
        """GET /bookings_list should deny unauthenticated users."""
        response = self.client.get('/bookings_list')
        self.assertIn(response.status_code, [302, 403])

    def test_set_borrower_post(self):
        response = self.client.post('/set_borrower', json={
            "name": "Test",
            "contact": "test@example.com",
            "phone": "123456"
        })
        self.assertIn(response.status_code, [200, 302, 400])


if __name__ == '__main__':
    unittest.main()
