import json
import unittest
from app import App, password_matches
import test_app
from test_app import PASSWORD, HASH

NEW = 'Local8!Test'


class AccountsTests(unittest.TestCase):
    setUp = test_app.ServiceTests.setUp
    request = test_app.ServiceTests.request
    login = test_app.ServiceTests.login

    def seed(self, name='alice', role='admin', enabled=1):
        with self.app.db() as db:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (name, HASH, role, enabled))

    def signin(self, name, password=PASSWORD):
        result = self.request('/waze/api/login', 'POST', {'username':name,'password':password})
        self.assertEqual(result['status'], 200)
        headers = {'HTTP_COOKIE':result['headers']['Set-Cookie'].split(';')[0]}
        headers['HTTP_X_CSRF_TOKEN'] = self.request('/waze/api/state', headers=headers)['json']['csrf']
        return headers

    def update(self, headers, **body):
        revision = self.request('/waze/api/users', headers=headers).get('json', {}).get('revision', 0)
        return self.request('/waze/api/users','POST',{'revision':revision,'adminPassword':PASSWORD,**body},headers)

    def test_create_private_hash_and_no_operational_changes(self):
        h, _ = self.login()
        with self.app.db() as db: before=self.app.state(db)
        response = self.update(h,action='create',username='persona',role='operator',password=NEW)
        self.assertEqual(response['status'], 200)
        self.assertNotIn(NEW,response['body'].decode())
        self.assertNotIn('password_hash',response['body'].decode())
        with self.app.db() as db:
            row=db.execute('SELECT * FROM accounts WHERE username="persona"').fetchone()
            self.assertTrue(password_matches(NEW,row['password_hash']))
            self.assertEqual(before,self.app.state(db))
            self.assertNotIn(NEW, json.dumps([dict(r) for r in db.execute('SELECT * FROM account_audit')]))
        self.signin('persona',NEW)

    def test_anonymous_operator_and_viewer_cannot_manage(self):
        self.assertEqual(self.request('/waze/api/users')['status'],401)
        self.assertEqual(self.request('/waze/api/users','POST',{})['status'],401)
        for role in ('operator','viewer'):
            self.seed(role,role);h=self.signin(role)
            self.assertEqual(self.request('/waze/api/users',headers=h)['status'],403)
            self.assertEqual(self.update(h,action='create',username='blocked',role='admin',password=NEW)['status'],403)

    def test_viewer_cannot_modify_any_state_action(self):
        self.seed('observer','viewer');h=self.signin('observer')
        for action in ('publish_closure','reopen_roads','activate','reopen','save_template','finish_run','enable_map_schedule','exclude_map_date','report_issue'):
            self.assertEqual(self.request('/waze/api/state','POST',{'action':action,'revision':0},h)['status'],403)
        self.assertEqual(self.request('/waze/api/state',headers=h)['status'],200)
        self.assertEqual(self.request('/waze/api/catalog',headers=h)['status'],200)
        self.assertEqual(self.request('/waze/api/logout','POST',{},h)['status'],200)

    def test_csrf_and_origin(self):
        h,_=self.login()
        for altered in ({**h,'HTTP_X_CSRF_TOKEN':''},{**h,'HTTP_ORIGIN':'https://evil.test'}):
            self.assertEqual(self.update(altered,action='create',username='blocked',role='admin',password=NEW)['status'],403)

    def test_confirmation_rate_limit(self):
        h,_=self.login()
        for _ in range(5):
            self.assertEqual(self.update(h,action='create',username='blocked',role='admin',password=NEW,adminPassword='wrong')['status'],403)
        self.assertEqual(self.update(h,action='create',username='blocked',role='admin',password=NEW)['status'],429)
        with self.app.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],0)

    def test_disable_reenable_never_revives_sessions(self):
        self.seed('staff','operator');old=self.signin('staff');h,_=self.login()
        self.assertEqual(self.update(h,action='access',username='staff',role='operator',enabled=False)['status'],200)
        self.assertEqual(self.request('/waze/api/state',headers=old)['status'],401)
        self.assertEqual(self.update(h,action='access',username='staff',role='operator',enabled=True)['status'],200)
        self.assertEqual(self.request('/waze/api/state',headers=old)['status'],401)
        self.signin('staff')

    def test_permission_change_invalidates_old_session(self):
        self.seed('staff','operator');old=self.signin('staff');h,_=self.login()
        self.assertEqual(self.update(h,action='access',username='staff',role='viewer',enabled=True)['status'],200)
        self.assertEqual(self.request('/waze/api/state',headers=old)['status'],401)
        new=self.signin('staff')
        self.assertEqual(self.request('/waze/api/state',headers=new)['json']['role'],'viewer')

    def test_reset_changes_password_and_revokes_session(self):
        self.seed('staff','operator');old=self.signin('staff');h,_=self.login()
        self.assertEqual(self.update(h,action='password',username='staff',password=NEW)['status'],200)
        self.assertEqual(self.request('/waze/api/state',headers=old)['status'],401)
        self.signin('staff',NEW)
        self.assertEqual(self.request('/waze/api/login','POST',{'username':'staff','password':PASSWORD})['status'],401)

    def test_emergency_protection_and_own_access(self):
        h,_=self.login()
        for action in ('access','password','create'):
            self.assertEqual(self.update(h,action=action,username='operador',role='viewer',enabled=False,password=NEW)['status'],409)
        self.seed();own=self.signin('alice')
        for role, enabled in [('viewer',True),('admin',False),('operator',True)]:
            self.assertEqual(self.update(own,action='access',username='alice',role=role,enabled=enabled)['status'],409)

    def test_conflict_and_duplicate_case(self):
        h,_=self.login()
        body={'action':'create','username':'Alice','role':'viewer','password':NEW}
        self.assertEqual(self.update(h,**body)['status'],200)
        self.assertEqual(self.update(h,**{**body,'username':'alice'})['status'],409)
        self.assertEqual(self.update(h,action='access',username='Alice',role='admin',enabled=True,revision=0)['status'],409)

    def test_validation_no_partial_mutation(self):
        h,_=self.login()
        for extra in ({'password':'weak'},{'username':'a'},{'username':'Operador'},{'username':'bad name'},{'role':'superuser'},{'password':None}):
            self.assertEqual(self.update(h,**{'action':'create','username':'person','role':'operator','password':NEW,**extra})['status'],409)
        with self.app.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM account_audit').fetchone()[0],0)

    def test_migration_preserves_old_accounts_and_sessions(self):
        self.seed('staff','operator',0);self.seed('adminuser','admin');h=self.signin('adminuser')
        with self.app.db() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM accounts ORDER BY username')]
            db.execute("CREATE TABLE legacy_accounts(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','operator')),enabled INTEGER NOT NULL DEFAULT 1)")
            db.execute('INSERT INTO legacy_accounts SELECT * FROM accounts');db.execute('DROP TABLE accounts');db.execute('ALTER TABLE legacy_accounts RENAME TO accounts')
        self.app=App(self.config)
        with self.app.db() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM accounts ORDER BY username')])
        self.assertEqual(self.request('/waze/api/state',headers=h)['status'],200)
        self.assertEqual(self.update(h,action='create',username='observer',role='viewer',password=NEW)['status'],200)

    def test_user_assets_and_new_portal(self):
        html=self.request('/waze/')['body'].decode()
        for asset in ('command.css','users.js'):
            self.assertIn('/assets/'+asset,html)
            self.assertEqual(self.request('/waze/assets/'+asset)['status'],200)
        self.assertIn('user-dialog',html)
        self.assertIn('overview-active',html)
