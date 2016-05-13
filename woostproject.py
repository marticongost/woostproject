#!/usr/bin/python
#-*- coding: utf-8 -*-
u"""

.. moduleauthor:: Martí Congost <marti.congost@whads.com>
"""
from argparse import ArgumentParser
from collections import OrderedDict
import os
import re
import shutil
import subprocess
import socket
from tempfile import mkdtemp
from contextlib import contextmanager

try:
    import gconf
except:
    gconf = None


class Installer(object):

    ports_file = "~/.woost-ports"
    first_automatic_port = 13000

    def __init__(self):
        commands = OrderedDict()

        for key in dir(self):
            value = getattr(self, key)
            if (
                isinstance(value, type)
                and issubclass(value, Installer.Command)
                and value.name
            ):
                command = value(self)
                setattr(self, command.name, command)
                commands[command.name] = command

        self.commands = commands

    def create_cli(self):
        parser = ArgumentParser()
        subparsers = parser.add_subparsers(
            dest = "command",
            metavar = "command"
        )

        for name, command in self.commands.iteritems():
            command_parser = subparsers.add_parser(name, help = command.help)
            command.setup_cli(command_parser)

        return parser

    def run_cli(self):
        cli = self.create_cli()
        args = cli.parse_args()
        command = self.commands[args.command]
        for key, value in vars(args).iteritems():
            setattr(command, key, value)
        command()

    def _exec(self, *args, **kwargs):
        self.message(" ".join(args), fg = "slate_blue")
        subprocess.check_call(args, **kwargs)

    def _sudo(self, *args):
        self._exec("sudo", *args)

    def _sudo_write(self, target, contents):
        temp_dir = mkdtemp()
        temp_file_name = os.path.join(temp_dir, "tempfile")
        with open(temp_file_name, "w") as temp_file:
            temp_file.write(contents)
        self._sudo("cp", temp_file_name, target)

    _cli_fg_codes = {
        "default": 39,
        "white": 37,
        "black": 30,
        "red": 31,
        "green": 32,
        "brown": 33,
        "blue": 34,
        "violet": 35,
        "turquoise": 36,
        "light_gray": 37,
        "dark_gray": 90,
        "magenta": 91,
        "bright_green": 92,
        "yellow": 93,
        "slate_blue": 94,
        "pink": 95,
        "cyan": 96,
    }

    _cli_bg_codes = {
        "default": 49,
        "black": 48,
        "red": 41,
        "green": 42,
        "brown": 43,
        "blue": 44,
        "violet": 45,
        "turquoise": 46,
        "light_gray": 47,
        "dark_gray": 100,
        "magenta": 101,
        "bright_green": 102,
        "yellow": 103,
        "slate_blue": 104,
        "pink": 105,
        "cyan": 106,
        "white": 107
    }

    _cli_style_codes = {
        "normal": 0,
        "bold": 1,
        "underline": 4,
        "inverted": 7,
        "hidden": 8,
        "strike_through": 9
    }

    def message(self, text, **style):
        print self.styled(text, **style)

    def heading(self, text):
        print
        print self.styled(">>>", fg = "pink"),
        print self.styled(text + "\n", style = "bold")

    def styled(
        self,
        string,
        fg = "default",
        bg = "default",
        style = "normal"):

        fg_code = self._cli_fg_codes.get(fg)
        bg_code = self._cli_bg_codes.get(bg)
        style_code = self._cli_style_codes.get(style)

        if fg_code is None or bg_code is None or style_code is None:
            warn(
                "Can't print using the requested style: %s %s %s"
                % (fg, bg, style)
            )
            return string
        else:
            return "\033[%d;%d;%dm%s\033[m" % (
                style_code,
                fg_code,
                bg_code,
                string
            )

    def normalize_indent(self, string):
        norm_lines = []
        indent = None
        found_content = False
        for line in string.split("\n"):

            # Drop empty lines before the first line with content
            if not found_content:
                if line.strip():
                    found_content = True
                else:
                    continue

            line = line.rstrip()
            if indent is None:
                norm_line = line.lstrip()
                if norm_line != line:
                    indent = line[:len(line) - len(norm_line)]
                norm_lines.append(norm_line)
            elif line.startswith(indent):
                norm_lines.append(line[len(indent):])
            else:
                norm_lines.append(line)
        return u"\n".join(norm_lines)

    def acquire_port(self, key, port = None):

        file_port = self.first_automatic_port
        ports_file = os.path.expanduser(self.ports_file)

        try:
            with open(ports_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        file_key, file_port = line.split()
                        file_port = int(file_port)
                        if file_key == key:
                            return file_port
        except:
            if not os.path.exists(ports_file):
                write_mode = "w"
            else:
                raise
        else:
            write_mode = "a"

        if port is None:
            port = file_port + 1

        with open(ports_file, write_mode) as f:
            f.write("%s %d\n" % (key, port))

        return port

    def get_package_version(self, package_name):
        try:
            output = subprocess.check_output(["dpkg", "-s", package_name])
        except subprocess.CalledProcessError:
            return None
        else:
            match = re.compile(r"Version: (\d+(\.\d+)*)").search(output)
            return tuple(match.group(1).split("."))

    class Command(object):

        name = None
        help = None

        def __init__(self, installer):
            self.installer = installer

        def setup_cli(self, parser):
            pass

        def __call__(self):
            pass

    class BootstrapCommand(Command):

        name = "bootstrap"
        help = """
            Install the system packages required by Woost and apply global
            configuration.
            """

        packages = [
            "mercurial",
            "build-essential",
            "python-dev",
            "python-pip",
            "python-setuptools",
            "python-imaging",
            "libxml2-dev",
            "libxslt1-dev",
            "ghostscript",
            "apache2",
            "lib32z1-dev"
        ]

        apache_modules = [
            "rewrite",
            "proxy",
            "proxy_http"
        ]

        def __call__(self):
            self.install_packages()
            self.setup_apache()
            self.secure_eggs_folder()

        def install_packages(self):
            self.installer.heading("Installing system packages")
            self.installer._sudo("apt-get", "install", "-y", *self.packages)

        def setup_apache(self):
            self.installer.heading("Global setup for the Apache webserver")
            self.enable_apache_modules()
            self.restart_apache()

        def enable_apache_modules(self):
            for module in self.apache_modules:
                self.installer._sudo("a2enmod", module)

        def restart_apache(self):
            self.installer._sudo("service", "apache2", "restart")

        def secure_eggs_folder(self):
            self.installer.heading("Securing eggs folder")
            eggs_folder = os.path.expanduser("~/.python-eggs")
            if os.path.exists(eggs_folder):
                os.chmod(eggs_folder, 0744)

    class InstallCommand(Command):

        steps = [
            "init_config",
            "create_virtual_environment",
            "install_libs",
            "create_project_skeleton",
            "install_website",
            "setup_database",
            "copy_uploads",
            "configure_apache",
            "add_hostname_to_hosts_file",
            "create_mercurial_repository",
            "create_launcher"
        ]

        website = None
        source_installation = None
        installation_id = None
        alias = None
        package = None
        vhost_name = None
        workspace = None
        woost_releases = {
            "joust": (2, 5),
            "kungfu": (2, 6),
            "lemmings": (2, 7),
            "metroid": (2, 8),
            "nethack": (2, 9),
            "outrun": (2, 10)
        }
        woost_version = "nethack"
        woost_version_specifier = None
        hostname = None
        deployment_scheme = "mod_rewrite"
        modify_hosts_file = False
        port = None
        zeo_port = None
        languages = ("en",)
        admin_email = "admin@localhost"
        admin_password = None
        extensions = ()
        base_id = None
        launcher = "auto"
        recreate_env = False
        mercurial = True
        python_version = "2.7"
        cocktail_versions = {
            "joust": "gin",
            "kungfu": "horilka",
            "lemmings": "izarra",
            "metroid": "izarra",
            "nethack": "komovica",
            "outrun": "komovica"
        }
        linked_system_packages = ["PIL", "PILcompat"]
        cocktail_repository = "https://bitbucket.org/whads/cocktail"
        woost_repository = "https://bitbucket.org/whads/woost"
        var_reg_expr = re.compile(r"--SETUP-(?P<key>[A-Z0-9_]+)--")
        root_dir = None
        virtual_env_dir = None
        project_env_script = None
        cocktail_outer_dir = None
        cocktail_dir = None
        woost_outer_dir = None
        woost_dir = None
        project_outer_dir = None
        project_dir = None
        project_scripts_dir = None
        static_dir = None
        python_bin = None
        empty_project_folders = [
            ["data"],
            ["static", "images"],
            ["image-cache"],
            ["views", "resources"],
            ["upload"],
            ["sessions"]
        ]

        project_env_template = """
            source ~/.bashrc
            source --SETUP-VIRTUAL_ENV_DIR--/bin/activate
            export COCKTAIL=--SETUP-COCKTAIL_DIR--
            export WOOST=--SETUP-WOOST_DIR--
            export SITE=--SETUP-PROJECT_DIR--
            alias "site-shell=ipython -i --SETUP-PROJECT_DIR--/scripts/shell.py"
            """

        setup_template = """
            from setuptools import setup, find_packages

            setup(
                name = "--SETUP-WEBSITE--",
                install_requires = [
                    "woost--SETUP-WOOST_VERSION_SPECIFIER--"
                ],
                packages = find_packages(),
                include_package_data = True,
                namespace_packages = --SETUP-NAMESPACE_PACKAGE_LIST--,
                zip_safe = False
            )
            """

        apache_2_vhost_template = """
            <VirtualHost *:80>

                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--

                RewriteEngine On

                ProxyRequests Off
                <Proxy *>
                    Order deny,allow
                    Allow from all
                </Proxy>
                ProxyPreserveHost On
                SetEnv proxy-nokeepalive 1

                RewriteRule ^/$ http://--SETUP-APP_SERVER_HOST--/ [P]

                RewriteCond %{QUERY_STRING} ^(.+)$
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                # Always serve CSS source and maps generated from SASS files dynamically
                RewriteRule ^(.*\.scss\.(css|map))$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                RewriteCond %{DOCUMENT_ROOT}/$1 !-f
                RewriteCond %{DOCUMENT_ROOT}/$1 !-d
                RewriteCond %{DOCUMENT_ROOT}/$1 !-s
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                <Location />
                    Order deny,allow
                    Allow from all
                </Location>

            </VirtualHost>
            """

        apache_2_4_vhost_template = """
            <VirtualHost *:80>

                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--

                RewriteEngine On
                ProxyPreserveHost On
                RewriteRule ^/$ http://--SETUP-APP_SERVER_HOST--/ [P]

                RewriteCond %{QUERY_STRING} ^(.+)$
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                # Always serve CSS source and maps generated from SASS files dynamically
                RewriteRule ^(.*\.scss\.(css|map))$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                RewriteCond %{DOCUMENT_ROOT}$1 !-f
                RewriteCond %{DOCUMENT_ROOT}$1 !-d
                RewriteCond %{DOCUMENT_ROOT}$1 !-s
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]

                <Location />
                    Require all granted
                </Location>

            </VirtualHost>
            """

        terminal_profile = None
        terminal_source_profile = "Default"
        terminal_profile_settings = {}

        terminal_script = None
        terminal_script_template = r"""
            #!/bin/bash
            SITE_SCRIPTS=--SETUP-PROJECT_SCRIPTS_DIR--
            PROJECT_ENV=--SETUP-PROJECT_ENV_SCRIPT--
            source $PROJECT_ENV
            /usr/bin/gnome-terminal \
                --sm-client-disable --disable-factory --class --SETUP-ALIAS-- \
                --profile --SETUP-TERMINAL_PROFILE-- \
                --tab -t ZEO --working-directory $SITE_SCRIPTS \
                --command="/bin/bash -c 'source $PROJECT_ENV; ./rundb.sh; /bin/bash --init-file $PROJECT_ENV'" \
                --tab -t HTTP --working-directory $SITE_SCRIPTS \
                --command="/bin/bash -c 'source $PROJECT_ENV; ./run.py; /bin/bash --init-file $PROJECT_ENV'" \
                --tab -t Cocktail --working-directory $COCKTAIL \
                --tab -t Woost --working-directory $WOOST \
                --tab -t --SETUP-ALIAS-- --working-directory $SITE \
                --tab -t Shell --working-directory $SITE_SCRIPTS \
                --command="/bin/bash -c 'source $PROJECT_ENV; ipython -i shell.py; /bin/bash --init-file $PROJECT_ENV'" \
            """
        launcher_icons = ()

        desktop_file = None
        desktop_file_template = """
            #!/usr/bin/env xdg-open
            [Desktop Entry]
            Version=1.0
            Name=--SETUP-ALIAS--
            Exec=--SETUP-TERMINAL_SCRIPT--
            Icon=--SETUP-ALIAS--
            Terminal=false
            Type=Application
            Categories=Application;
            StartupWMClass=--SETUP-ALIAS--
            """

        first_commit_message = u"Created the project."

        def _python(self, command):
            self.installer._exec(self.python_bin, "-c", command)

        def setup_cli(self, parser):

            parser.add_argument("website",
                help = "The name of the website to create."
            )

            parser.add_argument("--installation-id",
                help = """
                    A string that uniquely identifies this instance of the
                    website. Will be used during content synchronization across
                    site installations. Examples: D (for development), P (for
                    production), CS-JS (for John Smith at Cromulent Soft). If
                    not set, it defaults to the hostname.
                    """,
                default = self.installation_id
            )

            parser.add_argument("--alias",
                help = """
                    If given, the website will be installed under a different
                    identifier. This is useful to install multiple copies of
                    the same website on a single host. Each installation should
                    have a different installation_id and hostname.
                    """,
                default = self.alias
            )

            parser.add_argument("--package",
                help = """
                    The fully qualified name of the Python package that will
                    contain the website. Leave blank to use the website's name
                    as the name of its package.
                    """,
                default = self.package
            )

            parser.add_argument("--workspace",
                help = """
                    The root folder where the website should be installed. If
                    not given it defaults to the value of the WORKSPACE
                    environment variable. The installer will create a folder
                    for the website in the workspace folder, named after the
                    'alias' (if present) or 'website' parameters.
                    """
            )

            parser.add_argument("--woost-version",
                help = """
                    The version of Woost that the weebsite will be based on.
                    """,
                choices = sorted(self.woost_releases),
                default = self.woost_version
            )

            parser.add_argument("--hostname",
                help = """
                    The hostname that the website should respond to. Leaving
                    it blank will default to "website.localhost" (where
                    "website" is the alias or name of your website).
                    """,
                default = self.hostname
            )

            parser.add_argument("--deployment-scheme",
                help = """
                    Choose between different deployment strategies for the
                    application. The default option is to serve the website using
                    apache and mod_rewrite (useful during development). The
                    mod_wsgi option is better for production environments. The
                    cherrypy option self hosts the application using a single
                    process; this can be a useful alternative if installing
                    apache is not possible or desirable.
                    """,
                choices = ["mod_rewrite", "mod_wsgi", "cherrypy"],
                default = self.deployment_scheme
            )

            parser.add_argument("--modify-hosts-file",
                help = """
                    Activating this flag will modify the system "hosts" file to
                    make the given hostname map to the local host. This can be
                    useful for development environments that don't have access
                    to a local wildcarding DNS server.
                    """,
                action = "store_true",
                default = self.modify_hosts_file
            )

            parser.add_argument("--recreate-env",
                help = """
                    If enabled, the installer will delete and recrete the Python
                    virtual environment for the project (if one already exists).
                    """,
                action = "store_true",
                default = self.recreate_env
            )

            parser.add_argument("--port",
                help = """
                    The port that the application server will listen on. Leave
                    blank to obtain an incremental port.
                    """,
                type = int,
                default = self.port
            )

            parser.add_argument("--zeo-port",
                help = """
                    The port that the database server will listen on. Leave
                    blank to obtain an incremental port.
                    """,
                type = int,
                default = self.zeo_port
            )

            parser.add_argument("--launcher",
                help = """
                    Indicates if the installer should create a desktop launcher
                    for the website. The launcher will start a terminal window
                    with multiple tabs, one for each process or code repository
                    used by the website. When set to "auto", the launcher will
                    only be created if a suitable terminal program is found.
                    """,
                choices = ["yes", "no", "auto"],
                default = self.launcher
            )

            parser.add_argument("--launcher-icon",
                help = """
                    Path to the icon that should be used by the launcher. Can
                    be given multiple times, to provide icons in different
                    sizes. Icons should be PNG files; Acceptable image sizes
                    are 16x16, 32x32, 48x48, 128x128 and 256x256.
                    """,
                dest = "launcher_icons",
                metavar = "ICON",
                nargs = "+",
                default = self.launcher_icons
            )

            parser.add_argument("--mercurial",
                help = """
                    If enabled, the installer will automatically create a
                    mercurial repository for the new website.
                    """,
                action = "store_true",
                default = self.mercurial
            )

        def __call__(self):
            for step in self.steps:
                getattr(self, step)()

        def add_step(self, step, after = None, before = None):

            if not after and not before:
                raise ValueError("Must specify a position for step %r" % step)

            if after and before:
                raise ValueError(
                    "Can't specify both 'after' and 'before' when adding a "
                    "step"
                )

            if after:
                pos = self.steps.index(after)
                self.steps.insert(pos + 1, step)
            else:
                pos = self.steps.index(before)
                self.steps.insert(pos, step)

        def init_config(self):

            if not self.woost_version_specifier:
                release = self.woost_releases[self.woost_version]
                next_release = (release[0], release[1] + 1)
                self.woost_version_specifier = ">=%s,<%s" % (
                    ("%d.%d" % release),
                    ("%d.%d" % next_release)
                )

            if not self.alias:
                self.alias = self.website

            self.flat_website_name = self.website.lower().replace(".", "_")
            self.flat_website_alias = self.alias.lower().replace(".", "_")

            if self.workspace is None:
                self.workspace = os.environ["WORKSPACE"]

            if self.installation_id is None:
                self.installation_id = (
                    os.environ.get("WOOST_INSTALLATION_ID")
                    or socket.gethostname()
                )

            if not self.package:
                self.package = self.website.lower()

            self.namespace_packages = self.package.split(".")
            self.namespace_packages.pop(-1)
            self.namespace_package_list = [
                ".".join(self.namespace_packages[:i])
                for i in range(1, len(self.namespace_packages) + 1)
            ]

            if not self.vhost_name:
                self.vhost_name = self.flat_website_alias

            if not self.root_dir:
                self.root_dir = os.path.join(self.workspace, self.alias.lower())

            if not self.virtual_env_dir:
                self.virtual_env_dir = self.root_dir

            if not self.project_env_script:
                self.project_env_script = os.path.join(
                    self.root_dir,
                    "project-env"
                )

            if not self.python_bin:
                self.python_bin = os.path.join(
                    self.virtual_env_dir,
                    "bin", "python"
                )

            if not self.hostname:
                self.hostname = self.flat_website_alias

            if self.deployment_scheme == "cherrypy":
                self.app_server_hostname = self.hostname
            else:
                self.app_server_hostname = "127.0.0.1"

            # Acquire or restore ports
            if not self.port:
                self.port = self.installer.acquire_port(self.alias + "-web")

            if not self.zeo_port:
                self.zeo_port = self.installer.acquire_port(self.alias + "-db")

            self.app_server_host = "%s:%d" % (
                self.app_server_hostname,
                self.port
            )

            # Cocktail paths
            if not self.cocktail_outer_dir:
                self.cocktail_outer_dir = os.path.join(
                    self.virtual_env_dir,
                    "cocktail"
                )

            if not self.cocktail_dir:
                self.cocktail_dir = os.path.join(
                    self.cocktail_outer_dir,
                    "cocktail"
                )

            # Woost paths
            if not self.woost_outer_dir:
                self.woost_outer_dir = os.path.join(
                    self.virtual_env_dir,
                    "woost"
                )

            if not self.woost_dir:
                self.woost_dir = os.path.join(
                    self.woost_outer_dir,
                    "woost"
                )

            # Project paths
            if not self.project_outer_dir:
                self.project_outer_dir = os.path.join(
                    self.virtual_env_dir,
                    self.website.lower()
                )

            if not self.project_dir:
                self.project_dir = os.path.join(
                    self.project_outer_dir,
                    *self.package.split(".")
                )

            if not self.project_scripts_dir:
                self.project_scripts_dir = os.path.join(self.project_dir, "scripts")

            if not self.static_dir:
                self.static_dir = os.path.join(self.project_dir, "static")

            if self.woost_version >= "nethack":
                self.empty_project_folders.append(["static", "resources"])

            # Apache configuration
            self.apache_vhost_file = (
                "/etc/apache2/sites-available/"
                + self.vhost_name
            )
            self.apache_version = self.installer.get_package_version("apache2")

            if self.apache_version[:2] == ("2", "4"):
                self.apache_vhost_template = self.apache_2_4_vhost_template
                self.apache_vhost_file += ".conf"
            else:
                self.apache_vhost_template = self.apache_2_vhost_template

            # Terminal profile / launcher
            if not self.terminal_profile:
                self.terminal_profile = self.alias

            if not self.terminal_script:
                self.terminal_script = os.path.join(
                    self.root_dir,
                    "project-terminal"
                )

            self.terminal_profile_settings = \
                self.terminal_profile_settings.copy()

            self.terminal_profile_settings.setdefault(
                "visible_name",
                self.alias
            )
            self.terminal_profile_settings.setdefault(
                "title",
                self.alias
            )
            self.terminal_profile_settings.setdefault(
                "title_mode",
                "ignore"
            )
            self.terminal_profile_settings.setdefault(
                "use_custom_command",
                True
            )
            self.terminal_profile_settings.setdefault(
                "custom_command",
                "/bin/bash --init-file " + self.project_env_script
            )

            if not self.desktop_file:
                self.desktop_file = os.path.join(
                    os.path.expanduser("~"),
                    ".local",
                    "share",
                    "applications",
                    self.flat_website_alias + ".desktop"
                )

        def expand_vars(self, string):
            return self.var_reg_expr.sub(self._inject_var, string)

        def process_template(self, string):
            string = self.installer.normalize_indent(string)
            string = self.expand_vars(string)
            return string

        def _inject_var(self, match):
            key = match.group("key").lower()
            try:
                return unicode(getattr(self, key))
            except AttributeError:
                raise KeyError("Undefined variable: %s" % match.group(0))

        def create_virtual_environment(self):

            self.installer.heading(
                "Creating the project's virtual environment"
            )

            # Make sure virtualenv is installed
            try:
                from virtualenv import create_environment
            except ImportError:
                self.installer._sudo("pip", "install", "virtualenv")
                from virtualenv import create_environment

            # Remove the previous virtual environment
            if os.path.exists(self.virtual_env_dir):
                if not self.recreate_env:
                    self.installer.message("Preserving the existing environment")
                    return

                self.installer.message("Deleting the current environment")

                for dir in "bin", "include", "lib", "local":
                    old_dir = os.path.join(self.virtual_env_dir, dir)
                    if os.path.exists(old_dir):
                        shutil.rmtree(old_dir)

            # Create the new virtual environment
            create_environment(self.virtual_env_dir)

            # Install ipython
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "easy_install"
                ),
                "ipython==4.0.0"
            )

            # Link system packages into the virtual environment
            # (compiling PIL is out of the question...)
            for pkg in self.linked_system_packages:
                self.installer._sudo("ln", "-s",
                    os.path.join(
                        "/usr/lib/python%s/dist-packages" % self.python_version,
                        pkg
                    ),
                    os.path.join(
                        self.virtual_env_dir,
                        "lib/python%s/site-packages" % self.python_version
                    )
                )

            # Create the custom environment activation script
            with open(self.project_env_script, "w") as f:
                f.write(self.process_template(self.project_env_template))

        def install_libs(self):

            self.cocktail_version = self.cocktail_versions[self.woost_version]

            # TODO: Clone and setup PyStemmer with support for catalan

            # Clone and setup cocktail
            self.installer.heading("Installing cocktail")

            if not os.path.exists(
                os.path.join(self.cocktail_outer_dir, ".hg")
            ):
                self.installer._exec(
                    "hg", "clone",
                    self.cocktail_repository,
                    self.cocktail_outer_dir,
                    "-u", self.cocktail_version
                )

            self.setup_python_package(self.cocktail_outer_dir)

            # Clone and setup woost
            self.installer.heading("Installing woost")
            self.woost_outer_dir = \
                os.path.join(self.virtual_env_dir, "woost")

            if not os.path.exists(
                os.path.join(self.woost_outer_dir, ".hg")
            ):
                self.installer._exec(
                    "hg", "clone",
                    self.woost_repository,
                    self.woost_outer_dir,
                    "-u", self.woost_version
                )

            self.setup_python_package(self.woost_outer_dir)

        def setup_python_package(self, package_root):
            subprocess.Popen(
                "cd %s && source %s && python setup.py develop"
                % (
                    package_root,
                    os.path.join(self.virtual_env_dir, "bin", "activate")
                ),
                shell = True,
                executable = "/bin/bash"
            ).wait()

        def create_project_skeleton(self):

            self.installer.heading("Creating the project skeleton")

            # Copy source code from an existing installation using mercurial
            cloning_hg_repository = (
                self.source_installation
                and os.path.exists(
                    os.path.join(self.source_installation, ".hg")
                )
            )

            if (
                cloning_hg_repository
                and not os.path.exists(
                    os.path.join(self.project_outer_dir, ".hg")
                )
            ):
                self.installer._exec(
                    "hg", "clone", self.source_installation, self.project_outer_dir
                )

            # Create the package structure
            if not os.path.exists(self.project_outer_dir):
                os.mkdir(self.project_outer_dir)

            package_path = self.project_outer_dir
            for pkg in self.namespace_packages:
                package_path = os.path.join(package_path, pkg)
                if not os.path.exists(package_path):
                    os.mkdir(package_path)
                    pkg_file = os.path.join(package_path, "__init__.py")
                    open(pkg_file, "w").write(
                        "__import__('pkg_resources')"
                        ".declare_namespace(__name__)"
                    )

            if not os.path.exists(self.project_dir):
                os.mkdir(self.project_dir)

            # Create the filesystem structure
            skeleton = ProjectSkeleton()
            skeleton.processor = self.expand_vars
            skeleton.copy(
                os.path.join(self.woost_dir, "scripts", "project_skeleton"),
                self.project_dir
            )

            # Create empty folders
            for path_components in self.empty_project_folders:
                path = self.project_dir
                for path_component in path_components:
                    path = os.path.join(path, path_component)
                    if not os.path.exists(path):
                        os.mkdir(path)

            # Grant execution permission for project scripts
            for fname in os.listdir(self.project_scripts_dir):
                if fname != "__init__.py":
                    script = os.path.join(self.project_scripts_dir, fname)
                    if os.path.isfile(script):
                        os.chmod(script, 0774)

            # Create symbolic links to publish resource folders statically
            if self.woost_version >= "nethack":
                for link_name, source_dir in (
                    (
                        "cocktail",
                        os.path.join(self.cocktail_dir, "html", "resources")
                    ),
                    (
                        "woost",
                        os.path.join(self.woost_dir, "views", "resources")
                    ),
                    (
                        self.flat_website_name,
                        os.path.join(self.project_dir, "views", "resources")
                    )
                ):
                    target = os.path.join(self.static_dir, "resources", link_name)
                    if not os.path.exists(target):
                        os.symlink(source_dir, target)
            else:
                for link_name, source_dir in (
                    (
                        "cocktail",
                        os.path.join(self.cocktail_dir, "html", "resources")
                    ),
                    (
                        "resources",
                        os.path.join(self.woost_dir, "views", "resources")
                    ),
                    (
                        self.flat_website_name + "_resources",
                        os.path.join(self.project_dir, "views", "resources")
                    )
                ):
                    target = os.path.join(self.static_dir, link_name)
                    if not os.path.exists(target):
                        os.symlink(source_dir, target)

            # Write the setup file for the package
            with open(os.path.join(self.project_outer_dir, "setup.py"), "w") as f:
                setup_source = self.process_template(self.setup_template)
                f.write(setup_source)

            # Discard generated files that are managed with version control
            if cloning_hg_repository:
                self.installer._exec(
                    "hg", "revert", "--all", "--no-backup",
                    "-R", self.project_outer_dir
                )

        def install_website(self):
            self.installer.heading("Configuring the website's Python package")
            self.setup_python_package(self.project_outer_dir)

        def setup_database(self):
            if self.source_installation:
                self.copy_database()
            else:
                self.init_database()

        def copy_database(self):
            self.installer.heading("Copying database")
            shutil.copy(
                os.path.join(
                    self.source_installation,
                    *(
                        self.package.split(".")
                        + ["data", "database.fs"]
                    )
                ),
                os.path.join(self.project_dir, "data", "database.fs"),
            )

            # Change the hostname
            with self.zeo_process():
                self._python(
                    "'from %s.scripts.shell import config, datastore; "
                    "config.websites[0].hosts[0] = \"%s\"; "
                    "datastore.commit()'"
                    % (self.package, self.hostname)
                )

        @contextmanager
        def zeo_process(self):
            zeo_proc = subprocess.Popen([
                self.python_bin,
                os.path.join(self.virtual_env_dir, "bin", "runzeo"),
                "-f",
                os.path.join(self.project_dir, "data", "database.fs"),
                "-a",
                "127.0.0.1:%d" % self.zeo_port
            ])
            try:
                yield zeo_proc
            finally:
                zeo_proc.kill()

        def init_database(self):
            self.installer.heading("Initializing the database")

            with self.zeo_process():
                init_command = [
                    self.python_bin,
                    os.path.join(self.project_dir, "scripts", "initsite.py")
                ]

                if self.admin_email:
                    init_command.extend(["--user", self.admin_email])

                if self.admin_password:
                    init_command.extend(["--password", self.admin_password])

                if self.languages:
                    init_command.append("--languages=" + ",".join(self.languages))

                if self.extensions:
                    for extension in self.extensions:
                        init_command.extend(["--extension", extension])

                if self.installation_id:
                    init_command.extend([
                        "--installation-id",
                        self.installation_id
                    ])

                if self.hostname:
                    init_command.extend(["--hostname", self.hostname])

                if self.base_id:
                    init_command.extend(["--base-id", str(self.base_id)])

                self.installer._exec(*init_command)

        def copy_uploads(self):
            if self.source_installation:
                self.installer.heading("Copying uploads")
                source_folder = os.path.join(
                    self.source_installation,
                    *(
                        self.package.split(".")
                        + ["upload"]
                    )
                )
                dest_folder = os.path.join(self.project_dir, "upload")
                for file_name in os.listdir(source_folder):
                    item = os.path.join(source_folder, file_name)
                    if os.path.isfile(item):
                        shutil.copy(item, dest_folder)

                # Create links for static publication
                self._python(
                    "'from %s.scripts.shell import File, statipublication; "
                    "for f in File.select(): staticpublication.create_links(f)'"
                    % self.package
                )

        def configure_apache(self):

            if self.deployment_scheme == "cherrypy":
                return

            self.installer.heading("Configuring the site's Apache virtual host")

            self.installer._sudo_write(
                self.apache_vhost_file,
                self.get_apache_vhost_config()
            )
            self.installer._sudo("a2ensite", self.vhost_name)
            self.installer._sudo("service", "apache2", "restart")

            # TODO: mod_wsgi

        def get_apache_vhost_config(self):
            return self.process_template(self.apache_vhost_template)

        def create_mercurial_repository(self):

            if not self.mercurial:
                return

            if os.path.exists(os.path.join(self.project_outer_dir, ".hg")):
                return

            self.installer.heading(
                "Creating the project's mercurial repository"
            )

            # Initialize the repository
            self.installer._exec("hg", "init", self.project_outer_dir)

            # Create an .hgignore file
            hg_ignore_path = os.path.join(self.project_outer_dir, ".hgignore")
            with open(hg_ignore_path, "w") as f:
                f.write(self.get_mercurial_ignore_file_contents())

            # Add files and make a first commit
            self.installer._exec(
                "hg", "addremove",
                cwd = self.project_outer_dir
            )

            self.installer._exec(
                "hg", "commit", "-m",
                self.process_template(self.first_commit_message),
                cwd = self.project_outer_dir
            )

        def get_mercurial_ignore_file_contents(self):
            return u"\n".join(
                ["syntax: glob", "*.egg-info"]
                + [
                    os.path.relpath(content, self.project_outer_dir)
                    for content in [
                        os.path.join(self.project_dir, "settings.py"),
                        os.path.join(self.project_scripts_dir, "rundb.*"),
                        os.path.join(self.project_dir, "data"),
                        os.path.join(self.project_dir, "upload"),
                        self.static_dir,
                        os.path.join(self.project_dir, "image-cache"),
                        os.path.join(self.project_dir, ".session_key"),
                        os.path.join(self.project_dir, "sessions")
                    ]
                ]
            )

        def add_hostname_to_hosts_file(self):

            if not self.modify_hosts_file:
                return

            self.installer.heading("Modifying the system hosts file")

            hosts_file = "/etc/hosts"
            lines = list(open(hosts_file))

            # Make sure the hostname is not defined by the hosts file already
            for line in lines:

                # Strip comments
                pos = line.find("#")
                if pos >= 0:
                    line = line[:pos]

                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == self.hostname:
                    return

            # Append a definition for the hostname
            lines.append(
                "127.0.0.1\t%s # %s - Woost website\n"
                % (self.hostname, self.website)
            )
            self.installer._sudo_write(hosts_file, "".join(lines))

        def create_launcher(self):

            from PIL import Image

            if self.launcher == "no":
                return

            if gconf is None:
                if self.launcher == "yes":
                    raise OSError(
                        "Can't install a desktop launcher without the "
                        "'gconf' python package"
                    )
                else:
                    return

            if not os.path.exists("/usr/bin/gnome-terminal"):
                if self.launcher == "yes":
                    raise OSError(
                        "Can't install a desktop launcher without "
                        "gnome-terminal"
                    )
                else:
                    return

            self.installer.heading("Creating the project's desktop launcher")

            # Create a terminal profile
            c = gconf.client_get_default()
            root = "/apps/gnome-terminal/profiles/"

            for entry in c.all_entries(root + self.terminal_source_profile):
                k = entry.key.split("/")[-1]
                v = entry.value

                custom_value = self.terminal_profile_settings.get(k)

                if custom_value is not None:
                    if isinstance(custom_value, basestring):
                        v = gconf.Value(gconf.VALUE_STRING)
                        v.set_string(custom_value)
                    elif isinstance(custom_value, bool):
                        v = gconf.Value(gconf.VALUE_BOOL)
                        v.set_bool(custom_value)

                c.set(root + self.terminal_profile + "/" + k, v)

            profiles_key = "/apps/gnome-terminal/global/profile_list"
            profiles = c.get_list(profiles_key, "string")

            if self.terminal_profile not in profiles:
                profiles.append(self.terminal_profile)
                c.set_list(profiles_key, "string", profiles)

            # Terminal script
            with open(self.terminal_script, "w") as f:
                f.write(self.process_template(self.terminal_script_template))
            os.chmod(self.terminal_script, 0774)

            # Desktop file
            with open(self.desktop_file, "w") as f:
                f.write(self.process_template(self.desktop_file_template))
            os.chmod(self.desktop_file, 0774)

            # Launcher icon
            for icon_path in self.launcher_icons:
                print icon_path, os.path.join(
                        os.path.expanduser("~"),
                        ".local",
                        "share",
                        "icons",
                        "hicolor",
                        "%dx%d" % Image.open(icon_path).size,
                        "apps",
                        self.alias + ".png"
                    )
                shutil.copy(
                    icon_path,
                    os.path.join(
                        os.path.expanduser("~"),
                        ".local",
                        "share",
                        "icons",
                        "hicolor",
                        "%dx%d" % Image.open(icon_path).size,
                        "apps",
                        self.alias + ".png"
                    )
                )

    class NewCommand(InstallCommand):

        name = "new"
        help = "Create a new Woost website."

        def setup_cli(self, parser):

            Installer.InstallCommand.setup_cli(self, parser)

            parser.add_argument("--language", "-l",
                help = """
                    The list of languages for the website. Languages should be
                    indicated using two letter ISO codes.
                    """,
                dest = "languages",
                metavar = "LANG_ISO_CODE",
                nargs = "+",
                default = self.languages
            )

            parser.add_argument("--admin-email",
                help = "The e-mail for the administrator account.",
                default = self.admin_email
            )

            parser.add_argument("--admin-password",
                help = "The password for the administrator account.",
                default = self.admin_password
            )

            parser.add_argument("--extension", "-e",
                help = """The list of extensions to enable.""",
                dest = "extensions",
                metavar = "EXT_NAME",
                nargs = "+",
                default = self.extensions
            )

            parser.add_argument("--base-id",
                help = """
                    If set, the incremental ID of objects created by the
                    installer will start at the given value. Useful to prevent
                    collisions with old identifiers when importing data from an
                    existing website.
                    """,
                default = self.base_id
            )


    class CopyCommand(InstallCommand):

        name = "copy"
        help = "Create a new installation of an existing project."

        def setup_cli(self, parser):

            Installer.InstallCommand.setup_cli(self, parser)

            parser.add_argument("source_installation",
                help = """
                    Path to an existing installation of the project that should
                    be used to obtain the database, uploads and source code for
                    the project.
                    """
            )


class ProjectSkeleton(object):

    processor = lambda string: string

    def copy(self, source, target):

        # Copy folders recursively
        if os.path.isdir(source):
            if not os.path.exists(target):
                os.mkdir(target)
            for name in os.listdir(source):
                self.copy(
                    os.path.join(source, name),
                    os.path.join(target, self.processor(name))
                )
        # Copy files, expanding variables
        elif os.path.isfile(source):
            if os.path.splitext(source)[1] != ".pyc":
                with open(source, "r") as source_file:
                    source_data = source_file.read().decode("utf-8")
                    target_data = self.processor(source_data).encode("utf-8")
                    with open(target, "w") as target_file:
                        target_file.write(target_data)


if __name__ == "__main__":
    Installer().run_cli()

