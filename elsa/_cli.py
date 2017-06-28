import os
import sys
import urllib.parse
import warnings

from flask import Response
import flask_frozen
import click

from ._deployment import deploy as deploy_
from ._shutdown import ShutdownableFreezer, inject_shutdown


class Elsa:
    """Elsa freezes websites written in Flask"""

    DEFAULT_PORT = 8003
    DEFAULT_REMOTE = 'origin'

    def __init__(self, app, *, freezer=None, base_url=None):
        """Initialize Elsa.

        Arguments:

        * app: an instance of a Flask WSGI application
        * freezer:  flask_frozen.Freezer-like instance (optional)
        * base_url: URL for the application, used for external links (optional)
        """
        self.app = app
        self.freezer = freezer or ShutdownableFreezer(app)
        self.base_url = base_url

    def _base_url_help(self, *, freeze=False):
        freeze = ' with --freeze' if freeze else ''
        url = self.base_url
        default = 'default {}'.format(url) if url else 'mandatory' + freeze
        return 'URL for the application, used for external links, ' + default

    def _inject_cname(self):
        """Create CNAME route for GitHub pages"""
        @self.app.route('/CNAME')
        def cname():
            return Response(self.app.config['SERVER_NAME'],
                            mimetype='application/octet-stream')

    def _inject_shutdown(self):
        """Create a shutdown route"""
        inject_shutdown(self.app)

    def freeze(self, path=None, base_url=None):
        """Freeze the app

        Arguments:
        * path: location of frozen website (tries FREEZER_DESTINATION app
                config if not provided)
        * base_url: URL for the application, used for external links
                    (tries self.base_url and FREEZER_BASE_URL app config
                    if not provided)"""
        base_url = (base_url or self.base_url or
                    self.app.config.get('FREEZER_BASE_URL'))
        if not base_url:
            raise ValueError('No base URL provided')
        self.app.config['FREEZER_BASE_URL'] = base_url

        path = path or self.app.config.get('FREEZER_DESTINATION')
        self.app.config['FREEZER_DESTINATION'] = path
        self.app.config['SERVER_NAME'] = urllib.parse.urlparse(base_url).netloc

        if not path:
            raise ValueError('No path provided')

        # make sure Frozen Flask warnings are treated as errors
        warnings.filterwarnings('error',
                                category=flask_frozen.FrozenFlaskWarning)

        print('Generating HTML...')
        self.freezer.freeze()

    def freeze_fail_exit(self, path=None, base_url=None):
        """Like freeze() but exists on exception"""
        try:
            self.freeze(path, base_url)
        except flask_frozen.FrozenFlaskWarning as w:
            print('Error:', w, file=sys.stderr)
            sys.exit(1)

    def serve(self, port=DEFAULT_PORT):
        """Serve the frozen app using the freezer's ability to serve what was
        frozen"""
        return self.freezer.serve(port=port)

    def deploy(self, *, path=None, remote=None, push=False, show_err=False):
        """Deploy to GitHub pages, expects to be already frozen

        Arguments:
        * path: Where to find frozen HTML, defaults to FREEZER_DESTINATION app
                config
        * remote: (optional) git remote
        * push: whether to push to git
        * show_err: whether to display git push failure stderr"""
        path = path or self.app.config.get('FREEZER_DESTINATION')
        remote = remote or self.DEFAULT_REMOTE
        return deploy_(path, remote=remote, push=push,
                       show_err=show_err)

    @classmethod
    def _port_option(cls):
        return click.option(
            '--port', type=int, default=cls.DEFAULT_PORT,
            help='Port to listen at')

    @classmethod
    def _cname_option(cls):
        return click.option(
            '--cname/--no-cname', default=True,
            help='Whether to create the CNAME file, default is to create it')

    def _path_option(self):
        return click.option(
            '--path', default=os.path.join(self.app.root_path, '_build'),
            help='Input path, default _build')

    def cli(self):
        """Get a cli() function"""

        @click.group(context_settings=dict(help_option_names=['-h', '--help']),
                     help=__doc__)
        def command():
            pass

        @command.command()
        @self._port_option()
        @self._cname_option()
        def serve(port, cname):
            """Run a debug server"""

            # Workaround for https://github.com/pallets/flask/issues/1907
            auto_reload = self.app.config.get('TEMPLATES_AUTO_RELOAD')
            if auto_reload or auto_reload is None:
                self.app.jinja_env.auto_reload = True

            self._inject_shutdown()
            if cname:
                self._inject_cname()

            self.app.run(host='0.0.0.0', port=port, debug=True)

        @command.command()
        @self._path_option()
        @click.option('--base-url', default=self.base_url,
                      help=self._base_url_help())
        @click.option('--serve/--no-serve',
                      help='After building the site, run a server with it')
        @self._port_option()
        @self._cname_option()
        def freeze(path, base_url, serve, port, cname):
            """Build a static site"""
            if cname:
                self._inject_cname()

            if not base_url:
                raise click.UsageError('No base URL provided, use --base-url')
            self.freeze_fail_exit(path, base_url)

            if serve:
                self.serve(port=port)

        @command.command()
        @self._path_option()
        @click.option('--base-url', default=self.base_url,
                      help=self._base_url_help(freeze=True))
        @click.option('--remote', default=self.DEFAULT_REMOTE,
                      help='The name of the remote to push to, '
                      'default origin')
        @click.option('--push/--no-push', default=None,
                      help='Whether to push the gh-pages branch, '
                      'deprecated default is to push')
        @click.option('--freeze/--no-freeze', default=True,
                      help='Whether to freeze the site before deploying, '
                      'default is to freeze')
        @click.option('--show-git-push-stderr', is_flag=True,
                      help='Show the stderr output of `git push` failure, '
                           'might be dangerous if logs are public')
        @self._cname_option()
        def deploy(path, base_url, remote, push, freeze,
                   show_git_push_stderr, cname):
            """Deploy the site to GitHub pages"""
            if push is None:
                warnings.simplefilter('always')
                msg = ('Using deploy without explicit --push/--no-push is '
                       'deprecated. Assuming --push for now. In future '
                       'versions of elsa, the deploy command will not push to '
                       'the remote server by default. Use --push explicitly '
                       'to maintain current behavior.')
                warnings.warn(msg, DeprecationWarning)
                push = True
            if freeze:
                if cname:
                    self._inject_cname()
                if not base_url:
                    raise click.UsageError('No base URL provided, use '
                                           '--base-url')
                self.freeze_fail_exit(path, base_url)

            deploy_(path, remote=remote, push=push,
                    show_err=show_git_push_stderr)

        return command()


def cli(app, *, freezer=None, base_url=None):
    """Get a cli() function for provided app"""
    return Elsa(app, freezer=freezer, base_url=base_url).cli()
