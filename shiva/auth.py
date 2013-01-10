from flask import current_app as app
from flask.ext.login import LoginManager

login_manager = LoginManager()
login_manager.setup_app(app)


class User(object):
    pass


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)
