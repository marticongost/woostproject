#!/usr/bin/python
#-*- coding: utf-8 -*-
u"""

.. moduleauthor:: Martí Congost <marti.congost@whads.com>
"""
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from collections import OrderedDict
import sys
import os
import getpass
import re
import shutil
import subprocess
import socket
import uuid
import json
import tarfile
import base64
from pwd import getpwnam
from tempfile import mkdtemp
from contextlib import contextmanager


class DependencySet(object):

    def __init__(
        self,
        packages = None,
        python_packages = None,
        repositories = None,
        apache_modules = None
    ):
        self.packages = packages or []
        self.python_packages = python_packages or []
        self.repositories = repositories or []
        self.apache_modules = apache_modules or []

    def install(self, installer):

        if self.repositories:
            installer._install_packages("software-properties-common")

            for repository in self.repositories:
                installer._install_repository(repository)

        if self.packages:
            installer._install_packages(*self.packages)

        for python_package in self.python_packages:
            installer._install_python_package(python_package)

        for apache_mod in self.apache_modules:
            installer._enable_apache_module(apache_mod)


class Feature(DependencySet):

    def __init__(self, description, installed_by_default = False, **kwargs):
        DependencySet.__init__(self, **kwargs)
        self.description = description
        self.installed_by_default = installed_by_default


class LetsEncryptFeature(Feature):

    renewal_frequency = "weekly"
    renewal_command = "/usr/bin/certbot renew"

    def install(self, installer):
        Feature.install(self, installer)

        # Change permissions for the certificates directory
        # (otherwise Apache fails to start)
        installer._sudo("chmod", "755", "/etc/letsencrypt/archive")

        # Cronjob for certificate renewal
        cronjob_script = "/etc/cron.%s/lets-encrypt-renewal" % self.renewal_frequency
        installer._sudo_write(
            cronjob_script,
            installer.normalize_indent(
                """
                #!/bin/bash
                %s
                """ % self.renewal_command
            )
        )
        installer._sudo("chmod", "755", cronjob_script)


class Command(object):

    name = None
    help = None
    description = None
    disabled_parameters = []

    def __init__(self, installer):
        self.installer = installer
        self.disabled_parameters = list(self.disabled_parameters)

    def _arg_name(self, parameter):
        return "--" + parameter.replace("_", "-")

    def _param_matches_args(self, param, args):
        return param in args or self._arg_name(param) in args

    def setup_cli(self, parser):
        pass

    def add_argument(self, owner, *args, **kwargs):
        for param in self.disabled_parameters:
            if self._param_matches_args(param, args):
                break
        else:
            owner.add_argument(*args, **kwargs)

    def process_parameters(self, parameters):
        for key, value in parameters.iteritems():
            setattr(self, key, value)

    def __call__(self):
        pass


class Installer(object):

    ports_file = "~/.woost-ports"
    first_automatic_port = 13000

    def __init__(self):
        commands = OrderedDict()

        for key in dir(self):
            value = getattr(self, key)
            if (
                isinstance(value, type)
                and issubclass(value, Command)
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
            command_parser = subparsers.add_parser(
                name,
                help = command.help,
                description = command.description,
                formatter_class = RawDescriptionHelpFormatter
            )
            command.setup_cli(command_parser)

        return parser

    def run_cli(self):
        cli = self.create_cli()
        args = cli.parse_args()
        command = self.commands[args.command]
        command.process_parameters(vars(args))
        command()

    def _exec(self, *args, **kwargs):
        self.message(" ".join(args), fg = "slate_blue")
        subprocess.check_call(args, **kwargs)

    def _sudo(self, *args):
        if self._user_is_root():
            return self._exec(*args)
        else:
            self._exec("sudo", *args)

    def _user_is_root(self):
        return os.geteuid() == 0

    def _sudo_write(self, target, contents):
        if self._user_is_root():
            with open(target, "w") as file:
                file.write(contents)
        else:
            temp_dir = mkdtemp()
            temp_file_name = os.path.join(temp_dir, "tempfile")
            with open(temp_file_name, "w") as temp_file:
                temp_file.write(contents)
            self._sudo("cp", temp_file_name, target)

    def _get_service_script_path(self, name):
        return os.path.join("/etc", "init.d", name)

    def _create_service(self, name, script_content):
        script_path = self._get_service_script_path(name)
        self._sudo_write(script_path, script_content)
        self._sudo("/bin/chmod", "744", script_path)
        self._sudo("update-rc.d", name, "defaults")

    def _start_service(self, name):
        script_path = self._get_service_script_path(name)
        self._sudo(script_path, "start")

    def _stop_service(self, name):
        script_path = self._get_service_script_path(name)
        self._sudo(script_path, "stop")

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

    def _install_packages(self, *packages):
        self._sudo("apt-get", "install", "-y", *packages)

    def _install_repository(self, repository):
        self._sudo("add-apt-repository", "-y", "-u", repository)

    def _install_python_package(self, package):
        self._sudo("-H", "pip", "install", package)

    def _enable_apache_module(self, module):
        self._sudo("a2enmod", module)

    class BootstrapCommand(Command):

        name = "bootstrap"
        help = \
            "Install the system packages required by Woost and apply global " \
            "configuration."

        @property
        def description(self):
            return (
                self.help
                + "\n\nThe following features can be "
                  "selected / deselected using the "
                  "--with-feature / --without-feature parameters:\n\n"
                + "\n".join(
                    (
                        "  %s%s"
                        % (
                            (
                                key + (
                                    "*"
                                    if feature.installed_by_default
                                    else ""
                                ) + ":"
                            ).ljust(22),
                            feature.description
                        )
                    )
                    for key, feature in self.features.iteritems()
                )
            )

        dependencies = DependencySet(
            packages = [
                "build-essential",
                "python-dev",
                "python-pip",
                "python-setuptools",
                "python-imaging",
                "libxml2-dev",
                "libxslt1-dev",
                "apache2",
                "lib32z1-dev"
            ],
            apache_modules = [
                "rewrite",
                "proxy",
                "proxy_http",
                "macro"
            ]
        )

        features = OrderedDict([
            ("pdf", Feature(
                "Generate thumbnails of PDF files",
                installed_by_default = True,
                packages = ["ghostscript"]
            )),
            ("mercurial", Feature(
                "Create Mercurial repositories for Woost projects",
                installed_by_default = True,
                packages = ["mercurial"]
            )),
            ("launcher", Feature(
                "Create desktop launchers for Woost projects",
                packages = ["xtitle", "gnome-terminal"]
            )),
            ("mod_wsgi", Feature(
                "Deploy using mod_wsgi",
                packages = ["libapache2-mod-wsgi"],
                apache_modules = ["wsgi"]
            )),
            ("letsencrypt", LetsEncryptFeature(
                "Obtain and renew free SSL certificates",
                repositories = ["ppa:certbot/certbot"],
                packages = ["python-certbot-apache"],
                apache_modules = ["headers"]
            ))
        ])

        # Select all features flagged as 'installed by default'
        selected_features = None
        added_features = set()
        removed_features = set()

        def __call__(self):
            self.init_config()
            self.install_dependencies()
            self.secure_eggs_folder()

        def setup_cli(self, parser):

            self.add_argument(
                parser,
                "--with-feature",
                help = "Enables the specified feature.",
                choices = list(self.features),
                nargs = "+",
                metavar = "feature",
                dest = "added_features",
                default = set()
            )

            self.add_argument(
                parser,
                "--without-feature",
                help = "Disables the specified feature.",
                choices = list(self.features),
                nargs = "+",
                metavar = "feature",
                dest = "removed_features",
                default = set()
            )

        def init_config(self):
            self.selected_features = set(
                feature_id
                for feature_id, feature in self.features.iteritems()
                if feature.installed_by_default
            )
            self.selected_features.update(self.added_features)
            self.selected_features.difference_update(self.removed_features)

        def install_dependencies(self):

            self.installer.heading("Installing core dependencies")
            self.dependencies.install(self.installer)

            for feature_id in self.selected_features:
                feature = self.features[feature_id]
                self.installer.heading("Installing support for " + feature_id)
                feature.install(self.installer)

            self.installer.heading("Restarting Apache")
            self.installer._sudo("service", "apache2", "restart")

        def secure_eggs_folder(self):
            self.installer.heading("Securing eggs folder")
            eggs_folder = os.path.expanduser("~/.python-eggs")
            if os.path.exists(eggs_folder):
                os.chmod(eggs_folder, 0744)

    class InstallCommand(Command):

        preliminary_tasks = [
            "init_config",
            "become_dedicated_user"
        ]

        tasks = [
            "create_project_directories",
            "create_virtual_environment",
            "install_libs",
            "create_project_skeleton",
            "write_project_settings",
            "install_website",
            "setup_database",
            "copy_uploads",
            "configure_zeo_service",
            "configure_temp_files_purging",
            "configure_backup",
            "obtain_lets_encrypt_certificate",
            "configure_apache",
            "add_hostname_to_hosts_file",
            "create_mercurial_repository",
            "create_launcher"
        ]

        website = None
        environment = "development"

        environments = {
            "development": {
                "deployment_scheme": "mod_rewrite",
                "zodb_deployment_scheme": "zeo",
                "zeo_pack": False,
                "purge_temp_files": False,
                "backup": False,
                "cherrypy_env_global_config": []
            },
            "production": {
                "deployment_scheme": "mod_wsgi",
                "zodb_deployment_scheme": "zeo_service",
                "zeo_pack": True,
                "purge_temp_files": True,
                "backup": False,
                "cherrypy_env_global_config": [
                    '"engine.autoreload_on": False',
                    '"server.log_to_screen": False'
                ]
            }
        }

        source_installation = None
        dedicated_user = None
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
            "outrun": (2, 10),
            "pacman": (2, 11),
            "quake": (2, 12)
        }
        woost_version = "quake"
        woost_version_specifier = None
        hostname = None
        deployment_scheme = None
        modify_hosts_file = False
        port = None

        zodb_deployment_scheme = None
        zeo_port = None
        zeo_service_user = None
        zeo_service_name = None

        zeo_pack = None
        zeo_pack_days = 2
        zeo_pack_frequency = "00 04 * * *"
        zeo_pack_template = """
            #!/bin/bash
            PORT=--SETUP-ZEO_PORT--
            echo "Running zeopack on port $PORT"
            --SETUP-VIRTUAL_ENV_DIR--/bin/zeopack -h localhost -p $PORT -d --SETUP-ZEO_PACK_DAYS--
            echo "Done."
            sync
            """

        purge_temp_files = None
        purge_temp_files_frequency = "00 05 * * *"
        purge_temp_files_max_days = 3
        purge_temp_files_template = """
            #!/bin/bash
            FIND=/usr/bin/find
            PROJECT_DIR=--SETUP-PROJECT_DIR--
            MAX_DAYS=--SETUP-PURGE_TEMP_FILES_MAX_DAYS--
            $FIND $PROJECT_DIR/sessions -mtime +$MAX_DAYS -delete
            $FIND $PROJECT_DIR/upload/temp -mtime +$MAX_DAYS -delete
            """

        backup = None
        backup_dir = None
        backup_frequency = "30 04 * * *"
        backup_max_days = 3
        backup_template = r"""
            #!/bin/bash
            PROJECT_DIR=--SETUP-PROJECT_DIR--
            DEST=--SETUP-BACKUP_DIR--
            mkdir -p $DEST

            # Upload backup
            BACKUP_DATE=`date +%Y%m%d%H%M%S`
            UPLOAD_DIR=$PROJECT_DIR/upload

            # Upload backup
            /usr/bin/rsync \
                --exclude=temp \
                -abv \
                --delete-after \
                --backup-dir=$DEST/incremental/$BACKUP_DATE \
                $UPLOAD_DIR \
                $DEST/current

            # Database backup
            DB_FILE=$PROJECT_DIR/data/database.fs
            REPOZO=--SETUP-VIRTUAL_ENV_DIR--/bin/repozo
            FIND=/usr/bin/find
            mkdir -p $DEST/current/data

            # - Full backup
            DOW=`date +%a`
            if [ $DOW = "Sun" ]; then
                echo "Sunday full backup:"

                # Update full backup date
                date +%d-%b > $DEST/current/data/database-full-date

                $REPOZO -FBvzQ -r $DEST/current/data -f $DB_FILE

            # - Incremental backup
            else
                $REPOZO -BvzQ -r $DEST/current/data -f $DB_FILE
            fi

            # Delete old backups
            MAX_DAYS=--SETUP-BACKUP_MAX_DAYS--
            $FIND $DEST/current/data -type f -mtime $MAX_DAYS -delete
            $FIND $DEST/current/data -type d -empty -mtime $MAX_DAYS -delete
            $FIND $DEST/incremental -type f -mtime $MAX_DAYS -delete
            $FIND $DEST/incremental -type d -empty -mtime $MAX_DAYS -delete
            """

        languages = ("en",)
        admin_email = "admin@localhost"
        admin_password = None
        extensions = ()
        base_id = None
        launcher = "auto"
        recreate_env = False
        mercurial = False
        python_version = "2.7"
        cocktail_versions = {
            "joust": "gin",
            "kungfu": "horilka",
            "lemmings": "izarra",
            "metroid": "izarra",
            "nethack": "komovica",
            "outrun": "komovica",
            "pacman": "lambanog",
            "quake": "mezcal"
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
        launcher_dir = None
        python_bin = None
        python_lib_path = None
        useradd_script = "/usr/sbin/useradd"
        empty_project_folders = [
            ["data"],
            ["static", "images"],
            ["image-cache"],
            ["views", "resources"],
            ["upload"],
            ["sessions"]
        ]

        dedicated_user_bash_aliases_template = """
            export WORKSPACE="--SETUP-WORKSPACE--"
            export WOOST_INSTALLATION_ID="--SETUP-INSTALLATION_ID--"
            source --SETUP-PROJECT_ENV_SCRIPT--
        """

        project_env_template = """
            source --SETUP-VIRTUAL_ENV_DIR--/bin/activate
            export COCKTAIL=--SETUP-COCKTAIL_DIR--
            export WOOST=--SETUP-WOOST_DIR--
            export SITE=--SETUP-PROJECT_DIR--
            alias "site-shell=ipython --no-term-title -i --SETUP-PROJECT_DIR--/scripts/shell.py"
            alias "cml=python -m cocktail.html.templates.loader"
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

        settings_template = """
            from woost import app
            app.package = "--SETUP-PACKAGE--"
            app.installation_id = "--SETUP-INSTALLATION_ID--"

            # Application server configuration
            import cherrypy
            cherrypy.config.update({
                "global": {
                    ==SETUP-INCLUDE_CHERRYPY_GLOBAL_CONFIG==
                }
            })

            # Object store provider
            from cocktail.persistence import datastore
            from ZEO.ClientStorage import ClientStorage
            db_host = "127.0.0.1"
            db_port = --SETUP-ZEO_PORT--
            datastore.storage = lambda: ClientStorage((db_host, db_port))

            # Use file based sessions
            from cocktail.controllers import session
            session.config["session.type"] = "file"
            """

        settings_template_tail = [
            (
                """
                # Always recompile SASS files
                from cocktail.controllers.filepublication import SASSPreprocessor
                SASSPreprocessor.ignore_cached_files = True
                """,
                lambda cmd: cmd.environment == "development"
            ),
            (
                """
                # Reload inlined SVG files if they are modified
                from cocktail.html import inlinesvg
                inlinesvg.cache.updatable = True
                """,
                lambda cmd: cmd.environment == "development"
            ),
            (
                """
                # Disable CML template reloading
                from cocktail.html import templates
                templates.get_loader().cache.updatable = False
                """,
                lambda cmd: cmd.environment == "production"
            ),
            (
                """
                # Cache
                from cocktail.caching import RESTCacheStorage
                cache_storage = RESTCacheStorage("http://localhost:--SETUP-CACHE_SERVER_PORT--")
                app.cache.storage = cache_storage
                app.cache.verbose = True

                from cocktail.html import rendering_cache
                rendering_cache.storage = cache_storage
                rendering_cache.verbose = True
                """,
                lambda cmd: (
                    cmd.cache_enabled
                    and (
                        cmd.environment != "development"
                        or cmd.deployment_scheme == "mod_wsgi"
                    )
                )
            ),
            (
                """
                # Cache
                from cocktail.caching import RESTCacheStorage
                cache_storage = RESTCacheStorage("http://localhost:--SETUP-CACHE_SERVER_PORT--")
                app.cache.storage = cache_storage
                app.cache.verbose = True

                from cocktail.html import rendering_cache
                rendering_cache.storage = cache_storage
                rendering_cache.verbose = True
                """,
                lambda cmd: (
                    cmd.cache_enabled
                    and cmd.environment == "development"
                    and cmd.deployment_scheme != "mod_wsgi"
                )
            )
        ]

        cherrypy_global_config = [
            '"server.socket_host": "--SETUP-APP_SERVER_HOSTNAME--"',
            '"server.socket_port": --SETUP-PORT--',
            '"tools.encode.on": True',
            '"tools.encode.encoding": "utf-8"',
            '"tools.decode.on": True',
            '"tools.decode.encoding": "utf-8"'
        ]
        cherrypy_env_global_config = None

        zeo_service_script_template = """
            #!/bin/bash
            DESC="--SETUP-ALIAS-- ZEO"
            NAME=--SETUP-ZEO_SERVICE_NAME--
            USER=--SETUP-ZEO_SERVICE_USER--
            SCRIPTNAME=/etc/init.d/$NAME
            RUNDIR=/var/run/$NAME
            echo=/bin/echo
            RUNZEO="--SETUP-VIRTUAL_ENV_DIR--/bin/runzeo --pid-file $RUNDIR/$NAME.pid -f --SETUP-PROJECT_DIR--/data/database.fs -a 127.0.0.1:--SETUP-ZEO_PORT--"
            ZEOCTL="--SETUP-VIRTUAL_ENV_DIR--/bin/zeoctl -d -s $RUNDIR/$NAME.socket -u $USER"

            if [ `id -u` = 0 ]; then

                mkdir -p $RUNDIR
                chown $USER $RUNDIR

                case "$1" in
                  start)
                        $echo -n "Starting $DESC: $NAME "
                        $ZEOCTL -p "$RUNZEO" start
                        $echo "."
                        ;;
                  stop)
                        $echo -n "Stopping $DESC: $NAME "
                        $ZEOCTL -p "$RUNZEO" stop
                        echo "."
                        ;;
                  restart)
                        $echo -n "Restarting $DESC: $NAME "
                        $ZEOCTL -p "$RUNZEO" restart
                        echo "."
                        ;;
                  *)
                        $echo "Usage: $SCRIPTNAME {start|stop|restart}" >&2
                        exit 1
                        ;;
                esac
            else
                    echo "You MUST be root to execute this command"
            fi
        """

        apache_access_log = None
        apache_error_log = None
        apache_log_format = r"%v:%p %h %l %u %t \"%r\" %>s %O \"%{Referer}i\" \"%{User-Agent}i\" %T/%D"
        mod_wsgi_access_log = None
        mod_wsgi_error_log = None
        mod_wsgi_log_format = None

        logrotate_template = """
            ==SETUP-INCLUDE_LOG_DIR==/*.log {
                daily
                missingok
                rotate 14
                compress
                delaycompress
                notifempty
                create 640 root adm
                sharedscripts
                postrotate
                    if /etc/init.d/apache2 status > /dev/null ; then \
                        /etc/init.d/apache2 reload > /dev/null; \
                    fi;
                endscript
                prerotate
                    if [ -d /etc/logrotate.d/httpd-prerotate ]; then \
                            run-parts /etc/logrotate.d/httpd-prerotate; \
                    fi; \
                endscript
            }
            """

        vhost_macro_name = None

        apache_2_vhost_template = """
            <Macro --SETUP-VHOST_MACRO_NAME-->
                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--
                CustomLog --SETUP-APACHE_ACCESS_LOG-- "--SETUP-APACHE_LOG_FORMAT--"
                ErrorLog --SETUP-APACHE_ERROR_LOG--

                RewriteEngine On

                ProxyRequests Off
                <Proxy *>
                    Order deny,allow
                    Allow from all
                </Proxy>
                ProxyPreserveHost On
                SetEnv proxy-nokeepalive 1
                ==SETUP-INCLUDE_VHOST_REDIRECTION_RULES==
                <Location />
                    Order deny,allow
                    Allow from all
                </Location>
            </Macro>

            <VirtualHost *:80>
                Use --SETUP-VHOST_MACRO_NAME--
            </VirtualHost>
            """

        apache_2_4_vhost_template = """
            <Macro --SETUP-VHOST_MACRO_NAME-->
                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--
                CustomLog --SETUP-APACHE_ACCESS_LOG-- "--SETUP-APACHE_LOG_FORMAT--"
                ErrorLog --SETUP-APACHE_ERROR_LOG--

                RewriteEngine On
                ProxyPreserveHost On
                ==SETUP-INCLUDE_VHOST_REDIRECTION_RULES==
                <Location />
                    Require all granted
                </Location>
            </Macro>

            <VirtualHost *:80>
                Use --SETUP-VHOST_MACRO_NAME--
            </VirtualHost>
            """

        vhost_redirection_rules = [
            (
                """
                # Always serve the home page dynamically
                RewriteRule ^/$ http://--SETUP-APP_SERVER_HOST--/ [P]
                """,
                lambda cmd: True
            ),
            (
                """
                # Always serve requests with query string parameters
                # dynamically
                RewriteCond %{QUERY_STRING} ^(.+)$
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]
                """,
                lambda cmd: True
            ),
            (
                """
                # Always serve CSS source and maps generated from SASS files dynamically
                RewriteRule ^(.*\.scss\.(css|map))$ http://--SETUP-APP_SERVER_HOST--$1 [P]
                """,
                lambda cmd: cmd.environment == "development"
            ),
            (
                """
                # Only serve content dynamically if there is no file or folder
                # in the DocumentRoot that matches the request path
                RewriteCond %{DOCUMENT_ROOT}/$1 !-f
                RewriteCond %{DOCUMENT_ROOT}/$1 !-d
                RewriteCond %{DOCUMENT_ROOT}/$1 !-s
                RewriteRule ^(.*)$ http://--SETUP-APP_SERVER_HOST--$1 [P]
                """,
                lambda cmd: True
            )
        ]

        vhost_ssl_private_key_file = None
        vhost_ssl_certificate_file = None

        vhost_ssl_template = """
            <VirtualHost *:443>
                Use --SETUP-VHOST_MACRO_NAME--
                SSLEngine On
                SSLCertificateKeyFile --SETUP-VHOST_SSL_PRIVATE_KEY_FILE--
                SSLCertificateFile --SETUP-VHOST_SSL_CERTIFICATE_FILE--
                RequestHeader set X-Forwarded-Scheme "https"
            </VirtualHost>
            """

        mod_wsgi_vhost_template = r"""
            # mod_wsgi application
            Listen --SETUP-PORT--

            <VirtualHost --SETUP-APP_SERVER_HOST-->

                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--

                WSGIDaemonProcess --SETUP-MOD_WSGI_DAEMON_NAME-- \
                    user=--SETUP-MOD_WSGI_DAEMON_USER-- \
                    group=--SETUP-MOD_WSGI_DAEMON_GROUP-- \
                    processes=--SETUP-MOD_WSGI_DAEMON_PROCESSES-- \
                    threads=--SETUP-MOD_WSGI_DAEMON_THREADS-- \
                    display-name=--SETUP-MOD_WSGI_DAEMON_DISPLAY_NAME-- \
                    python-path=--SETUP-PYTHON_LIB_PATH-- \
                    python-eggs=--SETUP-MOD_WSGI_DAEMON_PYTHON_EGGS-- \
                    maximum-requests=--SETUP-MOD_WSGI_DAEMON_MAXIMUM_REQUESTS--

                WSGIProcessGroup --SETUP-MOD_WSGI_PROCESS_GROUP--
                WSGIApplicationGroup --SETUP-MOD_WSGI_APPLICATION_GROUP--
                WSGIImportScript --SETUP-PROJECT_SCRIPTS_DIR--/wsgi.py process-group=--SETUP-MOD_WSGI_PROCESS_GROUP-- application-group=--SETUP-MOD_WSGI_APPLICATION_GROUP--
                WSGIScriptAlias / --SETUP-PROJECT_SCRIPTS_DIR--/wsgiapp.py

                CustomLog --SETUP-MOD_WSGI_ACCESS_LOG-- "--SETUP-MOD_WSGI_LOG_FORMAT--"
                ErrorLog --SETUP-MOD_WSGI_ERROR_LOG--

                <Directory --SETUP-STATIC_DIR-->
                    Require all granted
                    WSGIProcessGroup --SETUP-MOD_WSGI_PROCESS_GROUP--
                </Directory>

                <Directory --SETUP-PROJECT_SCRIPTS_DIR-->
                    Require all granted
                </Directory>

            </VirtualHost>
            """

        mod_wsgi_daemon_name = None
        mod_wsgi_daemon_user = None
        mod_wsgi_daemon_group = None
        mod_wsgi_daemon_processes = 1
        mod_wsgi_daemon_threads = 10
        mod_wsgi_daemon_display_name = None
        mod_wsgi_daemon_python_eggs = None
        mod_wsgi_daemon_maximum_requests = 5000
        mod_wsgi_process_group = None
        mod_wsgi_application_group = None

        lets_encrypt = False

        cache_enabled = False
        cache_server_port = None
        cache_server_threads = 20
        cache_server_memory_limit = "128M"

        cache_server_vhost_template = r"""
            # cache server
            Listen --SETUP-CACHE_SERVER_PORT--

            <VirtualHost localhost:--SETUP-CACHE_SERVER_PORT-->

                ServerName localhost
                DocumentRoot --SETUP-STATIC_DIR--
                CustomLog /dev/null common

                WSGIDaemonProcess --SETUP-MOD_WSGI_DAEMON_NAME---cache \
                    user=--SETUP-MOD_WSGI_DAEMON_USER-- \
                    group=--SETUP-MOD_WSGI_DAEMON_GROUP-- \
                    processes=1 \
                    threads=--SETUP-CACHE_SERVER_THREADS-- \
                    display-name=--SETUP-MOD_WSGI_DAEMON_DISPLAY_NAME---cache \
                    python-path=--SETUP-PYTHON_LIB_PATH-- \
                    python-eggs=--SETUP-MOD_WSGI_DAEMON_PYTHON_EGGS--

                WSGIProcessGroup --SETUP-MOD_WSGI_PROCESS_GROUP---cache
                WSGIApplicationGroup --SETUP-MOD_WSGI_APPLICATION_GROUP---cache
                WSGIScriptAlias / --SETUP-PROJECT_SCRIPTS_DIR--/cacheserver.py

                <Directory --SETUP-PROJECT_SCRIPTS_DIR-->
                    Require all granted
                </Directory>

            </VirtualHost>
            """

        terminal_profile = None
        terminal_profile_settings = {}

        launcher_script = None
        launcher_tab_script = None

        launcher_tabs = [
            (
                "zeo",
                """
                #!/bin/bash
                export TAB_TITLE=ZEO
                export TAB_COMMAND='./rundb.sh'
                cd --SETUP-PROJECT_SCRIPTS_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: cmd.zodb_deployment_scheme == "zeo"
            ),
            (
                "http",
                """
                #!/bin/bash
                export TAB_TITLE=HTTP
                export TAB_COMMAND='python run.py'
                cd --SETUP-PROJECT_SCRIPTS_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: cmd.deployment_scheme != "mod_wsgi"
            ),
            (
                "cache",
                """
                #!/bin/bash
                export TAB_TITLE=Cache server
                export TAB_COMMAND='python cacheserver.py'
                cd --SETUP-PROJECT_SCRIPTS_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: (
                    cmd.cache_enabled
                    and cmd.deployment_scheme != "mod_wsgi"
                )
            ),
            (
                "cocktail",
                """
                #!/bin/bash
                export TAB_TITLE=Cocktail
                cd --SETUP-COCKTAIL_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: True
            ),
            (
                "woost",
                """
                #!/bin/bash
                export TAB_TITLE=Woost
                cd --SETUP-WOOST_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: True
            ),
            (
                "site",
                """
                #!/bin/bash
                export TAB_TITLE=Site
                cd --SETUP-PROJECT_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: True
            ),
            (
                "ipython",
                """
                #!/bin/bash
                export TAB_TITLE=IPython
                export TAB_COMMAND='site-shell'
                cd --SETUP-PROJECT_SCRIPTS_DIR--
                bash --init-file --SETUP-LAUNCHER_TAB_SCRIPT--
                """,
                lambda cmd: True
            )
        ]

        launcher_template = r"""
            #!/bin/bash
            LAUNCHER=--SETUP-VIRTUAL_ENV_DIR--/launcher
            /usr/lib/gnome-terminal/gnome-terminal-server --app-id info.woost.--SETUP-ALIAS-- --name --SETUP-ALIAS-- --class --SETUP-ALIAS-- &
            /usr/bin/gnome-terminal \
                --app-id info.woost.--SETUP-ALIAS-- \
                --SETUP-LAUNCHER_TERMINAL_TAB_PARAMETERS--
            """

        launcher_tab_template = r"""
            source ~/.bashrc
            source --SETUP-PROJECT_ENV_SCRIPT--

            function site-tab-title {
                xtitle "--SETUP-ALIAS--: $1"
            }

            if [[ -n "$TAB_TITLE" ]]; then
                site-tab-title $TAB_TITLE
            else
                xtitle --SETUP-ALIAS--
            fi

            if [[ -n "$TAB_COMMAND" ]]; then
                eval $TAB_COMMAND
            fi
        """

        launcher_icons = ()

        desktop_file = None
        desktop_file_template = """
            #!/usr/bin/env xdg-open
            [Desktop Entry]
            Version=1.0
            Name=--SETUP-ALIAS--
            Exec=--SETUP-LAUNCHER_SCRIPT--
            Icon=--SETUP-ALIAS--
            Terminal=false
            Type=Application
            Categories=Application;
            StartupWMClass=--SETUP-ALIAS--
            """

        mercurial_user = None
        first_commit_message = u"Created the project."

        def _python(self, source):
            temp_dir = mkdtemp()
            try:
                python_file = os.path.join(temp_dir, "module.py")
                with open(python_file, "w") as file:
                    file.write(self.installer.normalize_indent(source))
                self.installer._exec(self.python_bin, python_file)
            finally:
                shutil.rmtree(temp_dir)

        def setup_cli(self, parser):

            self.add_argument(
                parser,
                "website",
                help = "The name of the website to create."
            )

            self.add_argument(
                parser,
                "--environment",
                help = """
                    Choose between a development or a production environment.
                    This can change the defaults for several parameters, as
                    well as enable or disable certain features (such as
                    on screen logging, automatic reloading on code changes or
                    SASS recompilation). Defaults to %s.
                    """ % self.environment,
                choices = list(self.environments),
                default = self.environment
            )

            self.add_argument(
                parser,
                "--tasks",
                help = """
                    The tasks to execute. Useful to skip certain tasks when
                    ammending or fixing a previous installation. Default
                    sequence: %s
                    """ % " ".join(self.tasks),
                nargs = "+",
                metavar = "task",
                choices = list(self.tasks),
                default = self.tasks
            )

            self.add_argument(
                parser,
                "--recreate-env",
                help = """
                    If enabled, the installer will delete and recreate the Python
                    virtual environment for the project (if one already exists).
                    """,
                action = "store_true",
                default = self.recreate_env
            )

            parser.loc_group = parser.add_argument_group(
                "Location",
                "Options controlling the naming and placement of the "
                "application in the filesystem and in Python's package "
                "hierarchy."
            )

            self.add_argument(
                parser.loc_group,
                "--workspace",
                help = """
                    The root folder where the website should be installed. If
                    not given it defaults to the value of the WORKSPACE
                    environment variable. The installer will create a folder
                    for the website in the workspace folder, named after the
                    'alias' (if present) or 'website' parameters.
                    """
            )

            self.add_argument(
                parser.loc_group,
                "--alias",
                help = """
                    If given, the website will be installed under a different
                    identifier. This is useful to install multiple copies of
                    the same website on a single host. Each installation should
                    have a different installation_id and hostname.
                    """,
                default = self.alias
            )

            self.add_argument(
                parser.loc_group,
                "--package",
                help = """
                    The fully qualified name of the Python package that will
                    contain the website. Leave blank to use the website's name
                    as the name of its package.
                    """,
                default = self.package
            )

            self.add_argument(
                parser.loc_group,
                "--dedicated-user",
                help = """
                    Indicates that the project should be bound to a specific
                    OS user. The user will be created, if it doesn't exist, and
                    its bash initialization files will be changed in order to
                    set the appropiate environment variables and to activate
                    the project's virtual environment by default.
                    """
            )

            parser.cms_group = parser.add_argument_group(
                "CMS",
                "Options concerning the content and behavior of the CMS."
            )

            self.add_argument(
                parser.cms_group,
                "--installation-id",
                help = """
                    A string that uniquely identifies this instance of the
                    website. Will be used during content synchronization across
                    site installations. Examples: D (for development), P (for
                    production), CS-JS (for John Smith at Cromulent Soft). If
                    not set, it defaults to the value of the
                    WOOST_INSTALLATION_ID environment variable.
                    """,
                default = self.installation_id
            )

            self.add_argument(
                parser.cms_group,
                "--woost-version",
                help = """
                    The version of Woost that the website will be based on.
                    Defaults to '%s' (the latest stable version).
                    """ % self.woost_version,
                choices = sorted(self.woost_releases),
                default = self.woost_version
            )

            parser.deployment_group = parser.add_argument_group(
                "Deployment",
                "Options to control the deployment of the application."
            )

            self.add_argument(
                parser.deployment_group,
                "--deployment-scheme",
                help = """
                    Choose between different deployment strategies for the
                    application. The default option is to serve the website using
                    apache and mod_rewrite (useful during development). The
                    mod_wsgi option is better for production environments. The
                    cherrypy option self hosts the application using a single
                    process; this can be a useful alternative if installing
                    apache is not possible or desirable. Defaults to %s.
                    """ % (
                        self.deployment_scheme
                        or ", ".join(
                            "%s (%s)" % (defaults["deployment_scheme"], env)
                            for env, defaults in self.environments.iteritems()
                        )
                    ),
                choices = ["mod_rewrite", "mod_wsgi", "cherrypy"],
                default = self.deployment_scheme
            )

            self.add_argument(
                parser.deployment_group,
                "--hostname",
                help = """
                    The hostname that the website should respond to. Leaving
                    it blank will default to "website.localhost" (where
                    "website" is the alias or name of your website).
                    """,
                default = self.hostname
            )

            self.add_argument(
                parser.deployment_group,
                "--modify-hosts-file",
                help = """
                    Activating this flag will modify the system "hosts" file to
                    make the given hostname map to the local host. This can be
                    useful for development environments that don't have access
                    to a local wildcarding DNS server.
                    """,
                action = "store_true",
                default = self.modify_hosts_file
            )

            self.add_argument(
                parser.deployment_group,
                "--port",
                help = """
                    The port that the application server will listen on. Leave
                    blank to obtain an incremental port.
                    """,
                type = int,
                default = self.port
            )

            parser.db_group = parser.add_argument_group(
                "DB",
                "Options controlling the behavior of the database."
            )

            self.add_argument(
                parser.db_group,
                "--zodb-deployment-scheme",
                help = """
                    Indicates how to serve the application's ZODB database.
                    'zeo_service' configures a script to launch a ZEO server as
                    a daemon, using zeoctl. This makes sure the database server
                    is started when the system starts. The 'zeo' option expects
                    the server to be executed manually (for example, as one of
                    the tabs provided by the desktop launcher) or managed
                    elsewhere. Defaults to %s.
                    """ % (
                        self.zodb_deployment_scheme
                        or ", ".join(
                            "%s (%s)" % (defaults["zodb_deployment_scheme"], env)
                            for env, defaults in self.environments.iteritems()
                        )
                    ),
                choices = ["zeo", "zeo_service"],
                default = self.zodb_deployment_scheme
            )

            self.add_argument(
                parser.db_group,
                "--zeo-service-user",
                help = """
                    Sets the user that should run the ZEO server when
                    configured to run as a service
                    (--zodb-deployment-scheme='zeo_service). Defaults to
                    %s.
                    """ % (
                        self.zeo_service_user
                        or "--dedicated-user, if set, or to the current user "
                           "otherwise"
                    ),
                default = self.zeo_service_user
            )

            self.add_argument(
                parser.db_group,
                "--zeo-port",
                help = """
                    The port that the ZEO server will listen on. Leave
                    blank to obtain an incremental port.
                    """,
                type = int,
                default = self.zeo_port
            )

            serialize_bool = (
                lambda value: "enabled" if value else "disabled"
            )

            self.add_argument(
                parser.db_group,
                "--zeo-pack",
                action = "store_true"
            )

            self.add_argument(
                parser.db_group,
                "--no-zeo-pack",
                help = """
                    Enables or disables the packing of the ZODB database.
                    Defaults to %s.
                    """ % (
                        serialize_bool(self.zeo_pack)
                        if self.zeo_pack is not None
                        else ", ".join(
                            "%s (%s)" % (
                                serialize_bool(defaults["zeo_pack"]),
                                env
                            )
                            for env, defaults in self.environments.iteritems()
                        )
                    ),
                dest = "zeo_pack",
                action = "store_false"
            )

            self.add_argument(
                parser.db_group,
                "--zeo-pack-frequency",
                help = """
                    The frequency of database packing operations, specified in
                    crontab format. Defaults to '%s'.
                    """ % self.zeo_pack_frequency,
                default = self.zeo_pack_frequency
            )

            self.add_argument(
                parser.db_group,
                "--zeo-pack-days",
                help = """
                    The number of days that will be retained on the database
                    transaction journal after a pack operation. Defaults to
                    %d.
                    """ % self.zeo_pack_days,
                type = int,
                default = self.zeo_pack_days
            )

            parser.purge_temp_files_group = parser.add_argument_group(
                "Purge temporary files",
                "Options to control the automatic purge of temporary files."
            )

            self.add_argument(
                parser.purge_temp_files_group,
                "--purge-temp-files",
                action = "store_true"
            )

            self.add_argument(
                parser.purge_temp_files_group,
                "--no-purge-temp-files",
                help = """
                    Enables or disables the purging of the temporary files
                    generated by the website (old sessions and file uploads).
                    Defaults to %s.
                    """ % (
                        serialize_bool(self.purge_temp_files)
                        if self.purge_temp_files is not None
                        else ", ".join(
                            "%s (%s)" % (
                                serialize_bool(defaults["purge_temp_files"]),
                                env
                            )
                            for env, defaults in self.environments.iteritems()
                        )
                    ),
                dest = "purge_temp_files",
                action = "store_false"
            )

            self.add_argument(
                parser.purge_temp_files_group,
                "--purge-temp-files-frequency",
                help = """
                    The frequency of temporary file purging operations,
                    specified in crontab format. Defaults to '%s'.
                    """ % self.purge_temp_files_frequency,
                default = self.purge_temp_files_frequency
            )

            self.add_argument(
                parser.purge_temp_files_group,
                "--purge-temp-files-max-days",
                help = """
                    The maximum number of days that temporary files are
                    retained on the server. Defaults to %d.
                    """ % self.purge_temp_files_max_days,
                type = int,
                default = self.purge_temp_files_max_days
            )

            parser.backup_group = parser.add_argument_group(
                "Backup",
                "Options to control database and upload backups."
            )

            self.add_argument(
                parser.backup_group,
                "--backup",
                action = "store_true"
            )

            self.add_argument(
                parser.backup_group,
                "--no-backup",
                help = """
                    Enables or disables backup operations. Defaults to %s.
                    """ % (
                        serialize_bool(self.backup)
                        if self.backup is not None
                        else ", ".join(
                            "%s (%s)" % (
                                serialize_bool(defaults["backup"]),
                                env
                            )
                            for env, defaults in self.environments.iteritems()
                        )
                    ),
                dest = "backup",
                action = "store_false"
            )

            self.add_argument(
                parser.backup_group,
                "--backup-dir",
                help = """
                    The directory where backed up files should be stored.
                    Defaults to %s.
                    """ % (
                        self.backup_dir
                        or '~/backups on projects with a dedicated user, '
                           'to ROOT_DIR/backups otherwise'
                    ),
                default = self.backup_dir
            )

            self.add_argument(
                parser.backup_group,
                "--backup-frequency",
                help = """
                    The frequency of backup operations, specified in crontab
                    format. Defaults to '%s'.
                    """ % self.backup_frequency,
                default = self.backup_frequency
            )

            self.add_argument(
                parser.backup_group,
                "--backup-max-days",
                help = """
                    The maximum retention for backups, in days. Defaults to %d.
                    """ % self.backup_max_days,
                type = int,
                default = self.backup_max_days
            )

            parser.logging_group = parser.add_argument_group(
                "Logging",
                "Options to control application logs."
            )

            self.add_argument(
                parser.logging_group,
                "--apache-access-log",
                help = """
                    Sets the location of the access log for the Apache
                    webserver. %s.
                    """
                    % (
                        "Defaults to " + self.apache_access_log
                        if self.apache_access_log else
                        """
                        Defaults to ~/logs/apache/access.log when deploying
                        with a dedicated user, and to
                        /var/log/apache2/ALIAS-access.log otherwise.
                        """
                    ),
                default = self.apache_access_log
            )

            self.add_argument(
                parser.logging_group,
                "--apache-log-format",
                help = """
                    Sets the format for the access log of the Apache web server.
                    Defaults to '%s'.
                    """
                    % self.apache_log_format.replace("%", "%%"),
                default = self.apache_log_format
            )

            self.add_argument(
                parser.logging_group,
                "--apache-error-log",
                help = """
                    Sets the location of the error log for the Apache
                    webserver. %s.
                    """
                    % (
                        "Defaults to " + self.apache_error_log
                        if self.apache_error_log else
                        """
                        Defaults to ~/logs/apache/error.log when deploying
                        with a dedicated user, and to
                        /var/log/apache2/ALIAS-error.log otherwise.
                        """
                    ),
                default = self.apache_error_log
            )

            self.add_argument(
                parser.logging_group,
                "--mod-wsgi-access-log",
                help = """
                    Sets the location of the access log for the mod_wsgi
                    application. %s.
                    """
                    % (
                        "Defaults to " + self.mod_wsgi_access_log
                        if self.mod_wsgi_access_log else
                        """
                        Defaults to ~/logs/apache2/app-access.log when
                        deploying with a dedicated user, and to
                        /var/log/apache2/ALIAS-app-access.log otherwise.
                        """
                    ),
                default = self.mod_wsgi_access_log
            )

            self.add_argument(
                parser.logging_group,
                "--mod-wsgi-log-format",
                help = """
                    Sets the format of the access log for the mod_wsgi
                    application. Defaults to %s.
                    """
                    % (
                        self.mod_wsgi_log_format
                        or "the same format set for the apache access log."
                    ),
                default = self.mod_wsgi_log_format
            )

            self.add_argument(
                parser.logging_group,
                "--mod-wsgi-error-log",
                help = """
                    Sets the location of the error log for the mod_wsgi
                    application. %s.
                    """
                    % (
                        "Defaults to " + self.mod_wsgi_error_log
                        if self.mod_wsgi_error_log else
                        """
                        Defaults to ~/logs/apache/app-error.log when deploying
                        with a dedicated user, and to
                        /var/log/apache2/ALIAS-app-error.log otherwise.
                        """
                    ),
                default = self.apache_error_log
            )

            parser.launcher_group = parser.add_argument_group(
                "Launcher",
                "Options to create an application launcher for desktop "
                "environments."
            )

            self.add_argument(
                parser.launcher_group,
                "--launcher",
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

            self.add_argument(
                parser.launcher_group,
                "--launcher-icon",
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

            parser.mercurial_group = parser.add_argument_group(
                "Mercurial",
                "Options to create a Mercurial repository for the project."
            )

            self.add_argument(
                parser.mercurial_group,
                "--mercurial",
                help = """
                    If enabled, the installer will automatically create a
                    mercurial repository for the new website.
                    """,
                action = "store_true",
                default = self.mercurial
            )

            self.add_argument(
                parser.mercurial_group,
                "--mercurial-user",
                help = """
                    The user used to make the project's first commit. Defaults
                    to %s.
                    """
                    % (
                        self.mercurial_user
                        or "the value set in the Mercurial configuration files"
                    ),
                default = self.mercurial_user
            )

            parser.mod_wsgi_group = parser.add_argument_group(
                "mod_wsgi",
                "Parameters for mod_wsgi deployments "
                "(--deployment-scheme=mod_wsgi)."
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-name",
                help = """
                    The name of the daemon spawned by mod-wsgi. Defaults to the
                    project's alias.
                    """,
                default = self.mod_wsgi_daemon_name
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-user",
                help = """
                    The OS user that the daemon spawned by mod-wsgi will run
                    under. Defaults to %s.
                    """ % (
                        self.mod_wsgi_daemon_user
                        or "--dedicated-user, if set, or to the current user "
                           "otherwise"
                    ),
                default = self.mod_wsgi_daemon_user
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-group",
                help = """
                    The OS group that the daemon spawned by mod-wsgi will run
                    under. Defaults to %s.
                    """ % (
                        self.mod_wsgi_daemon_group
                        or "the effective value for --mod-wsgi-daemon-user"
                    ),
                default = self.mod_wsgi_daemon_group
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-processes",
                help = """
                    The number of processes available to the daemon spawned by
                    mod-wsgi. Defaults to %d.
                    """ % self.mod_wsgi_daemon_processes,
                default = self.mod_wsgi_daemon_processes
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-threads",
                help = """
                    The number of threads available to each process of the
                    daemon spawned by mod-wsgi. Defaults to %d.
                    """ % self.mod_wsgi_daemon_threads,
                default = self.mod_wsgi_daemon_threads
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-display-name",
                help = """
                    The display name for the processes of the daemon spawned
                    by mod-wsgi. Defaults to the project's alias.
                    """,
                default = self.mod_wsgi_daemon_display_name
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-python_eggs",
                help = """
                    A path to a directory that will be used by mod-wsgi to
                    store Python eggs.
                    """,
                default = self.mod_wsgi_daemon_python_eggs
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-daemon-maximum-requests",
                help = """
                    The maximum number of requests that will be served by each
                    process spawned by the mod-wsgi daemon before it is
                    replaced by a new process. Defaults to %d.
                    """ % self.mod_wsgi_daemon_maximum_requests,
                default = self.mod_wsgi_daemon_maximum_requests
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-process-group",
                help = """
                    The name of the process group for mod-wsgi.
                    """,
                default = self.mod_wsgi_process_group
            )

            self.add_argument(
                parser.mod_wsgi_group,
                "--mod-wsgi-application-group",
                help = """
                    The name of the application group for mod-wsgi.
                    """,
                default = self.mod_wsgi_application_group
            )

            parser.lets_encrypt_group = parser.add_argument_group(
                "Lets Encrypt",
                "Options to automate the installation of a SSL from Lets "
                "Encrypt."
            )

            self.add_argument(
                parser.lets_encrypt_group,
                "--lets-encrypt",
                action = "store_true"
            )

            self.add_argument(
                parser.lets_encrypt_group,
                "--no-lets-encrypt",
                help = """
                    Enables or disables the automatic installation of a Lets
                    Encrypt SSL certificate. Defaults to %s.
                    """ % serialize_bool(self.lets_encrypt),
                dest = "lets_encrypt",
                action = "store_false"
            )

            parser.cache_group = parser.add_argument_group(
                "Cache",
                "Setup content caching."
            )

            self.add_argument(
                parser.cache_group,
                "--cache-enabled",
                help =
                    """
                    Indicates if the project should enable content caching.
                    %s by default.
                    """ % (
                        "Enabled" if self.cache_enabled else "Disabled"
                    ),
                action = "store_true",
                default = self.cache_enabled
            )

            self.add_argument(
                parser.cache_group,
                "--cache-server-port",
                help =
                    "Sets the port that should be used by the cache server. "
                    + (
                        "Defaults to %d" % self.cache_server_port
                        if self.cache_server_port
                        else "If not set, a port is generated by default."
                    ),
                default = self.cache_server_port
            )

            self.add_argument(
                parser.cache_group,
                "--cache-server-threads",
                help =
                    "Sets the number of threads to be used by the cache "
                    "server process. Defaults to %d."
                    % self.cache_server_threads,
                default = self.cache_server_threads
            )

            self.add_argument(
                parser.cache_group,
                "--cache-server-memory-limit",
                help =
                    """
                    Sets the maximum amount of memory that can be used by the
                    cache server. The expected format is a number followed by
                    MB or GB suffixes. Defaults to %s.
                    """
                    % self.cache_server_memory_limit,
                default = self.cache_server_memory_limit
            )

        def __call__(self):

            for task in self.preliminary_tasks:
                getattr(self, task)()

            for task in self.tasks:
                getattr(self, task)()

        def add_task(self, task, after = None, before = None):

            if not after and not before:
                raise ValueError("Must specify a position for task %r" % task)

            if after and before:
                raise ValueError(
                    "Can't specify both 'after' and 'before' when adding a "
                    "task"
                )

            if after:
                pos = self.tasks.index(after)
                self.tasks.insert(pos + 1, task)
            else:
                pos = self.tasks.index(before)
                self.tasks.insert(pos, task)

        def add_preliminary_task(self, task, after = None, before = None):

            if not after and not before:
                raise ValueError("Must specify a position for task %r" % task)

            if after and before:
                raise ValueError(
                    "Can't specify both 'after' and 'before' when adding a "
                    "task"
                )

            if after:
                pos = self.preliminary_tasks.index(after)
                self.preliminary_tasks.insert(pos + 1, task)
            else:
                pos = self.preliminary_tasks.index(before)
                self.preliminary_tasks.insert(pos, task)

        def init_config(self):

            # Apply per-environment defaults
            for setting, default in \
            self.environments[self.environment].iteritems():
                if getattr(self, setting, None) is None:
                    setattr(self, setting, default)

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
                if self.dedicated_user:
                    self.workspace = "/home/" + self.dedicated_user
                else:
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

            if not self.vhost_macro_name:
                self.vhost_macro_name = self.vhost_name + "_vhost"

            if not self.root_dir:
                self.root_dir = os.path.join(
                    self.workspace,
                    "src" if self.dedicated_user else self.alias.lower()
                )

            if not self.virtual_env_dir:
                if self.dedicated_user:
                    self.virtual_env_dir = os.path.join(
                        self.workspace,
                        "vpython"
                    )
                else:
                    self.virtual_env_dir = self.root_dir

            self.python_lib_path = os.path.join(
                self.virtual_env_dir,
                "lib",
                "python2.7",
                "site-packages"
            )

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

            if self.cache_enabled and not self.cache_server_port:
                self.cache_server_port = \
                    self.installer.acquire_port(self.alias + "-cache")

            self.app_server_host = "%s:%d" % (
                self.app_server_hostname,
                self.port
            )

            # Cocktail paths
            if not self.cocktail_outer_dir:
                self.cocktail_outer_dir = os.path.join(
                    self.root_dir,
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
                    self.root_dir,
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
                    self.root_dir,
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

            # ZEO service
            self.zeo_service_name = self.alias + "-zeo"

            # Backup
            if not self.backup_dir:
                if self.dedicated_user:
                    self.backup_dir = os.path.join(
                        "/home",
                        self.dedicated_user,
                        "backups"
                    )
                else:
                    self.backup_dir = os.path.join(self.root_dir, "backups")

            # Apache configuration
            self.apache_vhost_file = (
                "/etc/apache2/sites-available/"
                + self.vhost_name
            )
            self.apache_version = self.installer.get_package_version("apache2")

            if self.apache_version[:2] == ("2", "4"):
                self.apache_vhost_template = self.apache_2_4_vhost_template
                self.apache_vhost_file += ".conf"

                if self.lets_encrypt:
                    cert_path = lambda *args: os.path.join(
                        "/etc",
                        "letsencrypt",
                        "live",
                        self.hostname,
                        *args
                    )
                    if int(self.apache_version[2]) >= 8:
                        self.vhost_ssl_private_key_file = \
                            cert_path("privkey.pem")
                        self.vhost_ssl_certificate_file = \
                            cert_path("fullchain.pem")
                    else:
                        self.vhost_ssl_private_key_file = \
                            cert_path("cert.pem")
                        self.vhost_ssl_certificate_file = \
                            cert_path("chain.pem")
            else:
                if self.deployment_scheme == "mod_wsgi":
                    sys.stderr.write(
                        "Deployment with mod_wsgi requires Apache 2.4\n"
                    )
                    sys.exit(1)

                if self.lets_encrypt:
                    sys.stderr.write(
                        "Lets Encrypt integration is only available with "
                        "Apache 2.4\n"
                    )
                    sys.exit(1)

                self.apache_vhost_template = self.apache_2_vhost_template

            # Apache / mod_wsgi log files
            if self.dedicated_user:
                log_pattern = (
                    "/home"
                    + self.dedicated_user
                    + "/logs/apache2/%s.log"
                )
            else:
                log_pattern = "/var/log/apache2/" + self.alias + "-%s.log"

            if not self.apache_access_log:
                self.apache_access_log = log_pattern % "access"

            if not self.apache_error_log:
                self.apache_error_log = log_pattern % "error"

            if not self.mod_wsgi_access_log:
                self.mod_wsgi_access_log = log_pattern % "app-access"

            if not self.mod_wsgi_error_log:
                self.mod_wsgi_error_log = log_pattern % "app-error"

            if not self.mod_wsgi_log_format:
                self.mod_wsgi_log_format = self.apache_log_format

            # Mod WSGI
            if self.deployment_scheme == "mod_wsgi":

                if not self.mod_wsgi_daemon_name:
                    self.mod_wsgi_daemon_name = self.alias

                if not self.mod_wsgi_daemon_display_name:
                    self.mod_wsgi_daemon_display_name = self.alias

                if not self.mod_wsgi_daemon_user:
                    self.mod_wsgi_daemon_user = \
                        self.dedicated_user or getpass.getuser()

                if not self.mod_wsgi_daemon_group:
                    self.mod_wsgi_daemon_group = self.mod_wsgi_daemon_user

                if not self.mod_wsgi_daemon_python_eggs:
                    self.mod_wsgi_daemon_python_eggs = os.path.join(
                        "/home",
                        self.mod_wsgi_daemon_user,
                        ".python-eggs"
                    )

                if not self.mod_wsgi_process_group:
                    self.mod_wsgi_process_group = self.alias

                if not self.mod_wsgi_application_group:
                    self.mod_wsgi_application_group = self.alias

            # Terminal profile / launcher
            if not self.terminal_profile:
                self.terminal_profile = self.alias

            if not self.launcher_script:
                self.launcher_script = os.path.join(
                    self.root_dir,
                    "launcher",
                    "launch"
                )

            if not self.launcher_tab_script:
                self.launcher_tab_script = os.path.join(
                    self.root_dir,
                    "launcher",
                    "tab"
                )

            self.terminal_profile_settings = \
                self.terminal_profile_settings.copy()

            self.terminal_profile_settings.setdefault(
                "visible-name",
                self.alias
            )
            self.terminal_profile_settings.setdefault(
                "title",
                self.alias
            )
            self.terminal_profile_settings.setdefault(
                "title-mode",
                "ignore"
            )
            self.terminal_profile_settings.setdefault(
                "use-custom-command",
                True
            )
            self.terminal_profile_settings.setdefault(
                "custom-command",
                "/bin/bash --init-file " + self.launcher_tab_script
            )

            self.launcher_tabs = [
                (key, cmd)
                for key, cmd, condition in self.launcher_tabs
                if condition(self)
            ]

            self.launcher_terminal_tab_parameters = u"\\\n\t".join(
                (
                    '--tab --profile %s --command="$LAUNCHER/tab-%s"'
                    % (self.terminal_profile, key)
                )
                for key, cmd in self.launcher_tabs
            )

            if not self.launcher_dir:
                self.launcher_dir = os.path.join(
                    self.root_dir,
                    "launcher"
                )

            if not self.desktop_file:
                self.desktop_file = os.path.join(
                    os.path.expanduser("~" + (self.dedicated_user or "")),
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

        def become_dedicated_user(self):

            if self.dedicated_user:

                if not self.installer._user_is_root():
                    sys.stderr.write(
                        "Configuring a dedicated user for the project requires "
                        "root access\n"
                    )
                    sys.exit(1)

                self.installer.heading("Setting up the dedicated user")

                # Create the user, if necessary
                try:
                    user_info = getpwnam(self.dedicated_user)
                except KeyError:
                    is_new = True
                    self.installer._sudo(
                        self.useradd_script,
                        "-m", # Create the home directory
                        "-U", # Create a group for the user
                        self.dedicated_user
                    )
                    user_info = getpwnam(self.dedicated_user)
                else:
                    is_new = False

                # Change the active user
                os.setegid(user_info.pw_gid)
                os.seteuid(user_info.pw_uid)
                os.environ["USER"] = self.dedicated_user
                os.environ["HOME"] = "/home/" + self.dedicated_user

                if is_new:
                    home = os.path.join("/home", self.dedicated_user)
                    bash_aliases = os.path.join(home, ".bash_aliases")
                    template = self.dedicated_user_bash_aliases_template
                    with open(bash_aliases, "w") as file:
                        file.write(self.process_template(template))

        def create_project_directories(self):

            if not os.path.exists(self.workspace):
                os.mkdir(self.workspace)

            if not os.path.exists(self.root_dir):
                os.mkdir(self.root_dir)

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
            if any(
                os.path.exists(os.path.join(self.virtual_env_dir, subfolder))
                for subfolder in ("bin", "include", "lib", "local", "share")
            ):
                if not self.recreate_env:
                    self.installer.message("Preserving the existing environment")
                    return

                self.installer.message("Deleting the current environment")

                for dir in "bin", "include", "lib", "local", "share":
                    old_dir = os.path.join(self.virtual_env_dir, dir)
                    if os.path.exists(old_dir):
                        shutil.rmtree(old_dir)

            # Create the new virtual environment
            create_environment(self.virtual_env_dir)

            # Upgrade setuptools
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "pip"
                ),
                "install",
                "--upgrade",
                "setuptools"
            )

            # Upgrade pip
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "pip"
                ),
                "install",
                "--upgrade",
                "pip"
            )

            # Install ipython
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "pip"
                ),
                "install",
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
            if self.source_installation:
                source_repository = os.path.join(
                    self.source_installation,
                    self.website.lower(),
                    ".hg"
                )
            else:
                source_repository = None

            if (
                source_repository
                and not os.path.exists(
                    os.path.join(self.project_outer_dir, ".hg")
                )
            ):
                self.installer._exec(
                    "hg", "clone",
                    os.path.join(self.source_installation, self.website.lower()),
                    self.project_outer_dir
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
            if source_repository:
                self.installer._exec(
                    "hg", "revert", "--all", "--no-backup",
                    "-R", self.project_outer_dir
                )

        def write_project_settings(self):

            self.installer.heading("Writing project settings")
            settings_script = os.path.join(self.project_dir, "settings.py")

            with open(settings_script, "w") as file:
                template = (
                    self.settings_template.replace(
                        "==SETUP-INCLUDE_CHERRYPY_GLOBAL_CONFIG==",
                        (",\n" + " " * 8).join(
                            self.cherrypy_global_config
                            + self.cherrypy_env_global_config
                        )
                    )
                    + "\n"
                    + self.installer.normalize_indent(
                        "".join(
                            snippet
                            for snippet, condition
                                in self.settings_template_tail
                            if condition(self)
                        )
                    )
                )
                file.write(self.process_template(template))

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
            self.import_database(
                os.path.join(
                    self.source_installation,
                    self.website.lower(),
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
                    """
                    from %s.scripts.shell import config, datastore
                    open("/tmp/debug", "a").write(config.websites[0].hosts[0] + "\\n")
                    config.websites[0].hosts[0] = "%s"
                    datastore.commit()
                    """
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
                    self.website.lower(),
                    *(
                        self.package.split(".")
                        + ["upload"]
                    )
                )
                dest_folder = os.path.join(self.project_dir, "upload")
                for file_name in os.listdir(source_folder):
                    item = os.path.join(source_folder, file_name)
                    if os.path.isfile(item):
                        self.import_upload(item, dest_folder)

                # Create links for static publication
                with self.zeo_process():
                    self._python(
                        """
                        from %s.scripts.shell import File, staticpublication
                        for f in File.select():
                            staticpublication.create_links(f)
                        """
                        % self.package
                    )

        def import_upload(self, src, dest):
            shutil.copy(src, dest)

        def import_database(self, src, dest):
            shutil.copy(src, dest)

        def configure_zeo_service(self):

            if self.zodb_deployment_scheme == "zeo_service":

                self.installer.heading(
                    "Installing a service for the ZEO database"
                )

                if not self.zeo_service_user:
                    self.zeo_service_user = \
                        self.dedicated_user or getpass.getuser()

                try:
                    self.installer._stop_service(self.zeo_service_name)
                except subprocess.CalledProcessError:
                    pass

                self.installer._create_service(
                    self.zeo_service_name,
                    self.get_zeo_service_script()
                )

                self.installer._start_service(self.zeo_service_name)

        def get_zeo_service_script(self):
            return self.process_template(self.zeo_service_script_template)

        def configure_zeo_pack(self):

            if self.zeo_pack:

                self.installer.heading("Configuring database packing")

                zeo_pack_script = os.path.join(
                    self.project_dir,
                    "scripts",
                    "zeopack.sh"
                )

                with open(zeo_pack_script, "w") as f:
                    f.write(self.process_template(self.zeo_pack_template))

                os.chmod(zeo_pack_script, 0744)
                cronjob = "%s %s" % (self.zeo_pack_frequency, zeo_pack_script)
                self.installer._exec(
                    "(crontab -l; echo '%s') | crontab -" % cronjob,
                    shell = True
                )

        def configure_temp_files_purging(self):

            if self.purge_temp_files:

                self.installer.heading("Configuring purging of temporary files")

                purge_script = os.path.join(
                    self.project_dir,
                    "scripts",
                    "purge-temp-files.sh"
                )

                with open(purge_script, "w") as f:
                    f.write(self.process_template(self.purge_temp_files_template))

                os.chmod(purge_script, 0744)
                cronjob = "%s %s" % (self.purge_temp_files_frequency, purge_script)
                self.installer._exec(
                    "(crontab -l; echo '%s') | crontab -" % cronjob,
                    shell = True
                )

        def configure_backup(self):

            if self.backup:

                self.installer.heading("Configuring backups")

                backup_script = os.path.join(
                    self.project_dir,
                    "scripts",
                    "backup.sh"
                )

                with open(backup_script, "w") as f:
                    f.write(self.process_template(self.backup_template))

                os.chmod(backup_script, 0744)
                cronjob = "%s %s" % (self.backup_frequency, backup_script)
                self.installer._exec(
                    "(crontab -l; echo '%s') | crontab -" % cronjob,
                    shell = True
                )

        def obtain_lets_encrypt_certificate(self):
            self.installer.heading("Obtaining Lets Encrypt SSL certificate")
            self.installer._sudo(
                "certbot", "certonly", "--webroot",
                "-d", self.hostname,
                "-w", self.static_dir
            )

        def configure_apache(self):

            if self.deployment_scheme == "cherrypy":
                return

            if self.deployment_scheme == "mod_wsgi":
                if not os.path.exists(self.mod_wsgi_daemon_python_eggs):
                    self.installer.heading(
                        "Creating and securing the eggs folder for mod_wsgi"
                    )
                    os.mkdir(self.mod_wsgi_daemon_python_eggs)
                    os.chmod(self.mod_wsgi_daemon_python_eggs, 0755)

            self.installer.heading("Configuring Apache logs")
            log_files = [
                self.apache_access_log,
                self.apache_error_log
            ]

            if self.deployment_scheme == "mod_wsgi":
                log_files.extend([
                    self.mod_wsgi_access_log,
                    self.mod_wsgi_error_log
                ])

            log_dirs = set(os.path.dirname(log_file) for log_file in log_files)

            for log_dir in log_dirs:
                self.installer._sudo("mkdir", "-p", log_dir)
                self.installer._sudo("chown", "root:root", log_dir)
                self.installer._sudo("chmod", "755", log_dir)

            logrotate_config = "\n".join(
                self.logrotate_template.replace(
                    "==SETUP-INCLUDE_LOG_DIR==",
                    log_dir
                )
                for log_dir in log_dirs if log_dir != "/var/log/apache2"
            )
            self.installer._sudo_write(
                os.path.join("/etc", "logrotate.d", self.alias),
                logrotate_config
            )

            self.installer.heading("Configuring the site's Apache virtual host")

            self.installer._sudo_write(
                self.apache_vhost_file,
                self.get_apache_vhost_config()
            )
            self.installer._sudo("a2ensite", self.vhost_name)
            self.installer._sudo("service", "apache2", "restart")

        def get_apache_vhost_config(self):

            template = self.apache_vhost_template.replace(
                "==SETUP-INCLUDE_VHOST_REDIRECTION_RULES==",
                "".join(
                    rule
                    for rule, condition in self.vhost_redirection_rules
                    if condition(self)
                )
            )

            if self.lets_encrypt:
                template += self.vhost_ssl_template

            if self.deployment_scheme == "mod_wsgi":
                template += u"\n" + self.mod_wsgi_vhost_template

                if self.cache_enabled:
                    template += u"\n" + self.cache_server_vhost_template

            return self.process_template(template)

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

            commit_command = [
                "hg", "commit", "-m",
                self.process_template(self.first_commit_message),
            ]

            if self.mercurial_user:
                commit_command.extend(["--user", self.mercurial_user])

            self.installer._exec(*commit_command, cwd = self.project_outer_dir)

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

            if self.launcher == "no":
                return

            if not os.path.exists("/usr/bin/gsettings"):
                if self.launcher == "yes":
                    raise OSError(
                        "Can't install a desktop launcher without gsettings"
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

            if self.dedicated_user:
                if self.launcher == "yes":
                    raise OSError(
                        "Can't install a desktop launcher for a dedicated user"
                    )
                else:
                    return

            self.installer.heading("Creating the project's desktop launcher")

            # Create a terminal profile
            profile_path = (
                "org.gnome.Terminal.Legacy.Profile:"
                "/org/gnome/terminal/legacy/profiles:/:%s/"
            )

            # Index terminal profiles by their title
            profile_list = []
            profile_uuid_map = {}
            profile_list_str = subprocess.check_output([
                "/usr/bin/gsettings",
                "get",
                "org.gnome.Terminal.ProfilesList",
                "list"
            ])

            for chunk in profile_list_str.strip()[1:-1].split(","):
                profile_uuid = chunk.strip().strip("'")
                profile_list.append(profile_uuid)
                profile_name = subprocess.check_output([
                    "/usr/bin/gsettings",
                    "get",
                    profile_path % profile_uuid,
                    "visible-name"
                ])[1:-2]
                profile_uuid_map[profile_name] = profile_uuid

            # Look for an existing profile
            try:
                profile_uuid = profile_uuid_map[self.alias]
            # Or create a new one
            except KeyError:
                profile_uuid = str(uuid.uuid4())
                profile_list.append(profile_uuid)
                subprocess.check_call([
                    "/usr/bin/gsettings",
                    "set",
                    "org.gnome.Terminal.ProfilesList",
                    "list",
                    "[%s]" % ", ".join(
                        "'%s'" % u
                        for u in profile_list
                    )
                ])

            # Profile settings
            source_profile_uuid = subprocess.check_output([
                "/usr/bin/gsettings",
                "get",
                "org.gnome.Terminal.ProfilesList",
                "default"
            ])
            source_profile_keys = subprocess.check_output([
                "/usr/bin/gsettings",
                "list-keys",
                profile_path % source_profile_uuid
            ]).split()

            for key in source_profile_keys:
                custom_value = self.terminal_profile_settings.get(key)
                if custom_value:
                    value = json.dumps(custom_value)
                else:
                    value = subprocess.check_output([
                        "/usr/bin/gsettings",
                        "get",
                        profile_path % source_profile_uuid,
                        key
                    ])
                subprocess.check_call([
                    "/usr/bin/gsettings",
                    "set",
                    profile_path % profile_uuid,
                    key,
                    value
                ])

            # Launcher scripts
            if not os.path.exists(self.launcher_dir):
                os.mkdir(self.launcher_dir)

            with open(self.launcher_script, "w") as f:
                f.write(self.process_template(self.launcher_template))

            os.chmod(self.launcher_script, 0774)

            with open(self.launcher_tab_script, "w") as f:
                f.write(self.process_template(self.launcher_tab_template))

            os.chmod(self.launcher_tab_script, 0774)

            for key, cmd in self.launcher_tabs:
                tab_file_path = os.path.join(self.launcher_dir, "tab-" + key)
                with open(tab_file_path, "w") as tab_file:
                    cmd = self.process_template(cmd)
                    tab_file.write(cmd)
                os.chmod(tab_file_path, 0774)

            # Desktop file
            desktop_file_path = os.path.dirname(self.desktop_file)
            self.installer._exec("mkdir", "-p", desktop_file_path)

            with open(self.desktop_file, "w") as f:
                f.write(self.process_template(self.desktop_file_template))
            os.chmod(self.desktop_file, 0774)

            # Launcher icon
            for icon_path in self.launcher_icons:
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
        description = help

        def setup_cli(self, parser):

            Installer.InstallCommand.setup_cli(self, parser)

            self.add_argument(
                parser.cms_group,
                "--language", "-l",
                help = """
                    The list of languages for the website. Languages should be
                    indicated using two letter ISO codes.
                    """,
                dest = "languages",
                metavar = "LANG_ISO_CODE",
                nargs = "+",
                default = self.languages
            )

            self.add_argument(
                parser.cms_group,
                "--admin-email",
                help = "The e-mail for the administrator account.",
                default = self.admin_email
            )

            self.add_argument(
                parser.cms_group,
                "--admin-password",
                help = "The password for the administrator account.",
                default = self.admin_password
            )

            self.add_argument(
                parser.cms_group,
                "--extension", "-e",
                help = """The list of extensions to enable.""",
                dest = "extensions",
                metavar = "EXT_NAME",
                nargs = "+",
                default = self.extensions
            )

            self.add_argument(
                parser.cms_group,
                "--base-id",
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
        description = help
        skip_database = False
        skip_uploads = False

        def setup_cli(self, parser):

            Installer.InstallCommand.setup_cli(self, parser)

            self.add_argument(
                parser,
                "source_installation",
                help = """
                    Path to an existing installation of the project that
                    should be used to obtain the database, uploads and
                    source code for the project.
                    """,
                default = self.source_installation
            )

            parser.copy_group = parser.add_argument_group(
                "Copy",
                "Options to control the copy process."
            )

            self.add_argument(
                parser.copy_group,
                "--skip-database",
                help = """Don't copy the database.""",
                action = "store_true"
            )

            self.add_argument(
                parser.copy_group,
                "--skip-uploads",
                help = u"""Don't copy uploaded files.""",
                action = "store_true"
            )

        def copy_database(self):
            if not self.skip_database:
                Installer.InstallCommand.copy_database(self)

        def copy_uploads(self):
            if not self.skip_uploads:
                Installer.InstallCommand.copy_uploads(self)

    class BundleCommand(CopyCommand):

        name = "bundle"
        help = "Create a self contained installer file for an existing " \
               "project."
        description = help

        undefined_parameter = object()

        output_file = "website.py"
        compression = "bz2"
        bundle_parameters = ["output_file", "compression"]
        unexportable_parameters = [
            "command",
            "output_file",
            "source_installation"
        ]
        disabled_parameters = [
            "tasks",
            "recreate-env"
        ]

        # Warning: this must be a multiple of 3!
        chunk_size = 8190

        def add_argument(self, owner, *args, **kwargs):

            # Ignore the default value for all arguments collected as
            # defaults for the unbundle operation
            for param in self.bundle_parameters:
                if self._param_matches_args(param, args):
                    break
            else:
                kwargs["default"] = self.undefined_parameter

            Installer.CopyCommand.add_argument(
                self,
                owner,
                *args,
                **kwargs
            )

        def setup_cli(self, parser):

            parser.bundle_group = parser.add_argument_group(
                "Bundle",
                "Options to control the generation of the installer bundle."
            )

            self.add_argument(
                parser.bundle_group,
                "--output-file",
                help = "The name of the generated file. Defaults to %s."
                    % self.output_file,
                default = self.output_file
            )

            self.add_argument(
                parser.bundle_group,
                "--compression",
                help = "The type of compression to use. Defaults to %s."
                    % self.compression,
                choices = ["gz", "bz2"],
                default = self.compression
            )

            Installer.CopyCommand.setup_cli(self, parser)

        def process_parameters(self, parameters):

            self.bundle_defaults = {}

            for key, value in parameters.iteritems():
                if (
                    key not in self.unexportable_parameters
                    and value is not self.undefined_parameter
                ):
                    self.bundle_defaults[key] = value

            Installer.CopyCommand.process_parameters(self, parameters)

        def __call__(self):

            with open(os.path.realpath(__file__), "r") as f:
                installer_src = f.read()

            main_pattern = 'if __name__ == "__main__":\n'
            pos = installer_src.index(main_pattern)

            self.installer.heading("Compressing bundle data")
            temp_dir = mkdtemp()

            try:
                tar_file_path = os.path.join(temp_dir, "bundle.tar")
                tar_file_mode = "w"
                if self.compression:
                    tar_file_mode += ":" + self.compression

                with tarfile.open(tar_file_path, tar_file_mode) as tar_file:
                    tar_file.add(
                        os.path.join(
                            self.source_installation,
                            self.website.lower()
                        ),
                        arcname = self.website.lower(),
                        recursive = True
                    )

                self.installer.heading("Generating self contained installer")
                installer_src = installer_src[:pos]

                with open(self.output_file, "w") as output_file:
                    write = output_file.write
                    write(installer_src)

                    # Embed the whole installation into a base 64 encoded
                    # triple string
                    write('BUNDLE_DATA = """')
                    with open(tar_file_path, "rb") as tar_file:
                        while True:
                            chunk = tar_file.read(self.chunk_size)
                            if chunk:
                                write(base64.b64encode(chunk))
                            else:
                                write('"""')
                                break

                    write('\nif __name__ == "__main__":\n')
                    write("    installer = BundleInstaller()\n")

                    for default in self.bundle_defaults.iteritems():
                        write("    installer.unbundle.%s = %r\n" % default)

                    write("    installer.run_cli()\n")
            finally:
                shutil.rmtree(temp_dir)


class BundleInstaller(Installer):

    def create_cli(self):
        parser = ArgumentParser()
        self.unbundle.setup_cli(parser)
        return parser

    def run_cli(self):
        cli = self.create_cli()
        args = cli.parse_args()
        self.bootstrap()
        self.unbundle.process_parameters(vars(args))
        self.unbundle()

    class UnbundleCommand(Installer.CopyCommand):
        name = "unbundle"

        # Warning: this must be a multiple of 4!
        chunk_size = 10920

        disabled_parameters = [
            "website",
            "source_installation"
        ]

        def __call__(self):
            self.installer.heading("Extracting bundle data")
            temp_dir = mkdtemp()
            self.source_installation = temp_dir
            try:
                self.extract_bundle_data(temp_dir)
                Installer.CopyCommand.__call__(self)
            finally:
                shutil.rmtree(temp_dir)

        def extract_bundle_data(self, dest):

            tar_file_path = os.path.join(dest, "website.tar")

            with open(tar_file_path, "wb") as tar_file:
                n = 0
                while True:
                    data = BUNDLE_DATA[n:n + self.chunk_size]
                    if not data:
                        break
                    tar_file.write(base64.b64decode(data))
                    n += self.chunk_size

            tar_file_mode = "r"
            if self.compression:
                tar_file_mode += ":" + self.compression

            with tarfile.open(tar_file_path, tar_file_mode) as tar_file:
                tar_file.extractall(dest)

        def import_upload(self, src, dest):
            self._move_file(src, dest)

        def import_database(self, src, dest):
            self._move_file(src, dest)

        def _move_file(self, src, dest):
            try:
                shutil.move(src, dest)
            # Might have been moved already if the command failed
            except shutil.Error:
                pass


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

