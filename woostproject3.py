#!/usr/bin/python
#-*- coding: utf-8 -*-
"""

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

python_version = "%s.%s" % sys.version_info[:2]


class Feature(object):

    version = 1
    installed_by_default = False
    packages = []
    python_packages = []
    repositories = []
    apache_modules = []

    def __init__(
        self,
        installer,
        id = None,
        description = None,
        installed_by_default = None,
        packages = None,
        python_packages = None,
        repositories = None,
        apache_modules = None
    ):
        self.__installer = installer

        if not id:
            id = self.__class__.__name__.lower()
            if id.endswith("feature"):
                id = id[:-len("feature")]

        self.__id = id

        if installed_by_default is not None:
            self.installed_by_default = installed_by_default

        if description is not None:
            self.description = description

        if packages is None:
            self.packages = list(self.packages)
        else:
            self.packages = packages

        if python_packages is None:
            self.python_packages = list(self.python_packages)
        else:
            self.python_packages = python_packages

        if repositories is None:
            self.repositories = list(self.repositories)
        else:
            self.repositories = repositories

        if apache_modules is None:
            self.apache_modules = list(self.apache_modules)
        else:
            self.apache_modules = apache_modules

    @property
    def installer(self):
        return self.__installer

    @property
    def id(self):
        return self.__id

    def install(self):
        self.installer.heading("Installing feature " + self.__id)
        self.install_dependencies()

    def install_dependencies(self):

        if self.repositories:
            self.installer._install_packages("software-properties-common")

            for repository in self.repositories:
                self.installer._install_repository(repository)

        if self.packages:
            self.installer._install_packages(*self.packages)

        for python_package in self.python_packages:
            self.installer._install_python_package(python_package)

        if self.apache_modules:
            for apache_mod in self.apache_modules:
                self.installer._enable_apache_module(apache_mod)

            self.installer._sudo("service", "apache2", "restart")

    @property
    def version_file(self):
        return os.path.join(self.installer.config_dir, "features", self.id)

    def write_version(self):
        self.installer._sudo_write(self.version_file, str(self.version))

    def get_installed_version(self):
        try:
            with open(self.version_file, "r") as feature_file:
                return int(feature_file.read())
        except (IOError, ValueError):
            return 0

    def disable(self):
        self.installer._sudo_write(self.version_file, "-1")

    def is_disabled(self):
        return self.get_installed_version() == -1

    def needs_update(self):
        installed_version = self.get_installed_version()
        return installed_version != -1 and installed_version < self.version

    def update(self):

        self.installer.require_config_dir()

        installed_version = self.get_installed_version()
        new_version = None

        if installed_version != -1 and installed_version < self.version:
            self.install()
            self.write_version()
            new_version = self.version

        return installed_version, new_version


class Core3Feature(Feature):
    description = "Basic packages required to install Woost projects."
    installed_by_default = True
    packages = [
        "build-essential",
        "python3-dev",
        "python3-pip",
        "python3-setuptools",
        "python3-pil",
        "libxml2-dev",
        "libxslt1-dev",
        "lib32z1-dev"
    ]

    def install(self):
        Feature.install(self)
        self.secure_eggs_folder()

    def secure_eggs_folder(self):
        eggs_folder = os.path.expanduser("~/.python-eggs")
        if os.path.exists(eggs_folder):
            os.chmod(eggs_folder, 0o744)


class PDFRendererFeature(Feature):
    description = "Generate thumbnails for PDF files."
    installed_by_default = True
    packages = ["ghostscript"]


class ApacheFeature(Feature):
    description = "Serve Woost websites with the Apache webserver."
    packages = [
        "apache2"
    ]
    apache_modules = [
        "rewrite",
        "proxy",
        "proxy_http",
        "macro",
        "expires"
    ]


class ModWSGIFeature(Feature):
    description = "Deploy using mod_wsgi"
    packages = ["libapache2-mod-wsgi"]
    apache_modules = ["wsgi"]


class ModWSGIExpressFeature(Feature):
    description = "Deploy using mod_wsgi_express"
    packages = ["apache2-dev"]


class LetsEncryptFeature(Feature):

    description = "Obtain and renew free SSL certificates"
    repositories = ["ppa:certbot/certbot"]
    packages = ["python-certbot-apache"]
    apache_modules = ["headers", "ssl"]

    renewal_frequency = "weekly"
    renewal_command = "/usr/bin/certbot renew"

    def install(self):
        Feature.install(self)

        # Change permissions for the certificates directory
        # (otherwise Apache fails to start)
        lets_encrypt_path = "/etc/letsencrypt/archive"
        self.installer._sudo("mkdir", "-p", lets_encrypt_path)
        self.installer._sudo("chmod", "755", lets_encrypt_path)

        # Cronjob for certificate renewal
        cronjob_script = "/etc/cron.%s/lets-encrypt-renewal" % self.renewal_frequency
        self.installer._sudo_write(
            cronjob_script,
            self.installer.normalize_indent(
                """
                #!/bin/bash
                %s
                """ % self.renewal_command
            )
        )
        self.installer._sudo("chmod", "755", cronjob_script)


class MercurialFeature(Feature):
    description = "Create Mercurial repositories for Woost projects"
    packages = ["mercurial"]


class LauncherFeature(Feature):
    description = "Create desktop launchers for Woost projects"
    packages = ["xtitle", "gnome-terminal"]

    def is_supported(self):
        return os.path.exists("/usr/bin/X")


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
                return False

        owner.add_argument(*args, **kwargs)
        return True

    def add_boolean_argument(self, owner, name, help = None, **kwargs):

        default_value = getattr(self, name)

        if self.add_argument(
            owner,
            self._arg_name(name),
            action = "store_true",
            default = default_value,
            **kwargs
        ):
            if help:
                serialize_bool = (
                    lambda value: "enabled" if value else "disabled"
                )
                help += " Defaults to %s." % (
                    serialize_bool(default_value)
                    if default_value is not None
                    else ", ".join(
                        "%s (%s)" % (
                            serialize_bool(defaults[name]),
                            env
                        )
                        for env, defaults in self.environments.items()
                    )
                )

            self.add_argument(
                owner,
                self._arg_name("no_" + name),
                action = "store_false",
                default = default_value,
                dest = name,
                help = help,
                **kwargs
            )

    def process_parameters(self, parameters):
        for key, value in parameters.items():
            setattr(self, key, value)

    def __call__(self):
        pass


class Installer(object):

    config_dir = "/etc/woost"
    ports_file = os.path.join(config_dir, "ports")
    legacy_ports_file = os.path.expanduser("~/.woost-ports")
    first_automatic_port = 14000
    __os_release = None

    def __init__(self):

        self.features = OrderedDict()

        for feature_class in (
            Core3Feature,
            PDFRendererFeature,
            ApacheFeature,
            ModWSGIFeature,
            ModWSGIExpressFeature,
            LetsEncryptFeature,
            MercurialFeature,
            LauncherFeature
        ):
            feature = feature_class(self)
            self.features[feature.id] = feature

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

    def require_config_dir(self):

        if not os.path.exists(self.config_dir):
            self._sudo("mkdir", "-p", self.config_dir)
            self._sudo("mkdir", "-p", os.path.join(self.config_dir, "features"))

            # Legacy support: copy ~/.woost-ports to /etc/woost/ports
            if os.path.exists(self.legacy_ports_file):
                self._sudo("cp", self.legacy_ports_file, self.ports_file)
                self._sudo("chown", "root:root", self.ports_file)
            else:
                self._sudo("touch", self.ports_file)

            self._sudo("chmod", "777", self.ports_file)

    def create_cli(self):
        parser = ArgumentParser()
        subparsers = parser.add_subparsers(
            dest = "command",
            metavar = "command"
        )

        for name, command in self.commands.items():
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

        if not args.command:
            cli.print_help()
            sys.exit(1)

        command = self.commands[args.command]
        command.process_parameters(vars(args))
        command()

    def get_os_release(self):

        if self.__os_release is None:
            try:
                self.__os_release = \
                    subprocess.check_output(["lsb_release", "-r", "-s"])
            except subprocess.CalledProcessError:
                raise OSError("Can't determine operating system release")

        return self.__os_release

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

    def _install_cronjob(self, cronjob, user):
        if user:
            self._exec(
                "(crontab -l -u %s; echo '%s') | crontab -u %s -" % (
                    user,
                    cronjob,
                    user
                ),
                shell = True
            )
        else:
            self._exec(
                "(crontab -l; echo '%s') | crontab -" % cronjob,
                shell = True
            )

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
        print(self.styled(text, **style))

    def heading(self, text):
        print()
        print(self.styled(">>>", fg = "pink"), end=' ')
        print(self.styled(text + "\n", style = "bold"))

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
        return "\n".join(norm_lines)

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
            output = output.decode("utf-8")
            match = re.compile(r"Version: (\d+(\.\d+)*)").search(output)
            return tuple(match.group(1).split("."))

    def _install_packages(self, *packages):

        pkg_list = []

        for pkg in packages:
            if isinstance(pkg, tuple):
                assert len(pkg) == 2
                pkg_name, condition = pkg
                if condition(self):
                    pkg_list.append(pkg_name)
            else:
                pkg_list.append(pkg)

        self._sudo("apt-get", "install", "-y", *pkg_list)

    def _install_repository(self, repository):
        self._sudo("add-apt-repository", "-y", "-u", repository)

    def _install_python_package(self, package):
        self._sudo("-H", "pip%s" % python_version, "install", package)

    def _enable_apache_module(self, module):
        self._sudo("a2enmod", module)

    class FeatureCommand(Command):

        name = "feature"
        help = "Install, update or disable global features and " \
               "configuration required by Woost."

        @property
        def description(self):
            return (
                self.help
                + "\n\nAvailable features:\n\n"
                + "\n".join(
                    (
                        "  %s%s" % (
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
                    for key, feature in self.installer.features.items()
                )
                + "\n\nFeatures marked with a * character are installed by "
                  "default; other features will\nbe installed if needed."
            )

        def setup_cli(self, parser):

            self.add_argument(
                parser,
                "feature",
                help = "Selects the feature to operate on.",
                metavar = "feature",
                choices = list(self.installer.features)
            )

            parser.action_group = \
                parser.add_mutually_exclusive_group(required = True)

            self.add_argument(
                parser.action_group,
                "--update",
                help = "Installs or updates the selected feature.",
                dest = "action",
                action = "store_const",
                const = "update"
            )

            self.add_argument(
                parser.action_group,
                "--disable",
                help = """
                    Disables the selected feature, preventing it from being
                    automatically installed in the future by an operation that
                    has it as a dependency.
                    """,
                dest = "action",
                action = "store_const",
                const = "disable"
            )

        def __call__(self):
            feature = self.installer.features[self.feature]
            if self.action == "update":
                feature.update()
            elif self.action == "disable":
                feature.disable()

    class InstallCommand(Command):

        _is_child_process = False

        preliminary_tasks = [
            "apply_environment_presets",
            "update_features",
            "init_config",
            "become_dedicated_user"
        ]

        cleanup_tasks = [
            "restore_original_user"
        ]

        tasks = [
            "create_project_directories",
            "create_virtual_environment",
            "install_libs",
            "install_extensions",
            "create_project_skeleton",
            "write_project_settings",
            "install_website",
            "setup_database",
            "copy_uploads",
            "configure_zeo_service",
            "configure_zeo_pack",
            "configure_temp_files_purging",
            "configure_backup",
            "configure_apache",
            "obtain_lets_encrypt_certificate",
            "configure_apache_https",
            "configure_mod_wsgi_express",
            "configure_mod_wsgi_express_cache_server",
            "add_hostname_to_hosts_file",
            "create_mercurial_repository",
            "create_launcher"
        ]

        skipped_tasks = []

        website = None
        environment = "development"

        python_packages_url = "https://woost.info/wheels"

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
                "deployment_scheme": "mod_wsgi_express",
                "zodb_deployment_scheme": "zeo_service",
                "zeo_pack": True,
                "purge_temp_files": True,
                "backup": True,
                "cherrypy_env_global_config": [
                    '"engine.autoreload_on": False',
                    '"server.log_to_screen": False'
                ]
            }
        }

        source_installation = None
        source_repository = None
        uploads_repository = None
        dedicated_user = None
        dedicated_user_shell = "/bin/bash"
        _original_uid = None
        installation_id = None
        alias = None
        package = None
        vhost_name = None
        workspace = None
        root_host = None
        default_root_host = "localhost"
        revision = None
        woost_version = "3.0.dev"
        woost_dependency_specifier = None
        woost_release_number = None
        extensions = []
        default_extensions_repository = \
            "https://bitbucket.org/whads/woost.extensions.%s"
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
            --SETUP-VIRTUAL_ENV_DIR--/bin/zeopack -h 127.0.0.1 -p $PORT -d --SETUP-ZEO_PACK_DAYS--
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
        cocktail_version = None
        cocktail_versions = {
            "3.0": "2.0.dev"
        }
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
                    "woost--SETUP-WOOST_DEPENDENCY_SPECIFIER--"
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
                lambda cmd: cmd.cache_enabled
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
            ### BEGIN INIT INFO
            # Provides:            --SETUP-ALIAS--
            # Required-Start:      $remote_fs $syslog
            # Required-Stop:       $remote_fs $syslog
            # Should-Start:        $local_fs
            # Should-Stop:         $local_fs
            # Default-Start:       2 3 4 5
            # Default-Stop:        0 1 6
            # Short-Description:   Start zeo daemon
            # Description:         Start up zeo
            ### END INIT INFO

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

        mod_wsgi_express_root = None
        mod_wsgi_express_service_name = None
        mod_wsgi_express_service_template = """
            #!/bin/bash
            ### BEGIN INIT INFO
            # Provides:            --SETUP-MOD_WSGI_EXPRESS_SERVICE_NAME--
            # Required-Start:      $remote_fs $syslog
            # Required-Stop:       $remote_fs $syslog
            # Should-Start:        $local_fs
            # Should-Stop:         $local_fs
            # Default-Start:       2 3 4 5
            # Default-Stop:        0 1 6
            # Short-Description:   Start the Mod WSGI Express daemon for --SETUP-ALIAS--
            # Description:         Start the Mod WSGI Express daemon for --SETUP-ALIAS--
            ### END INIT INFO

            --SETUP-MOD_WSGI_EXPRESS_ROOT--/apachectl $1
        """

        mod_wsgi_express_cacheserver_root = None
        mod_wsgi_express_cacheserver_service_name = None
        mod_wsgi_express_cacheserver_service_template = """
            #!/bin/bash
            ### BEGIN INIT INFO
            # Provides:            --SETUP-MOD_WSGI_EXPRESS_CACHESERVER_SERVICE_NAME--
            # Required-Start:      $remote_fs $syslog
            # Required-Stop:       $remote_fs $syslog
            # Should-Start:        $local_fs
            # Should-Stop:         $local_fs
            # Default-Start:       2 3 4 5
            # Default-Stop:        0 1 6
            # Short-Description:   Start the Mod WSGI Express daemon for the cache server of --SETUP-ALIAS--
            # Description:         Start the Mod WSGI Express daemon for the cache server of --SETUP-ALIAS--
            ### END INIT INFO

            --SETUP-MOD_WSGI_EXPRESS_CACHESERVER_ROOT--/apachectl $1
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

        apache_2_4_vhost_template = """
            <Macro --SETUP-VHOST_MACRO_NAME-->
                ServerName --SETUP-HOSTNAME--
                DocumentRoot --SETUP-STATIC_DIR--
                CustomLog --SETUP-APACHE_ACCESS_LOG-- "--SETUP-APACHE_LOG_FORMAT--"
                ErrorLog --SETUP-APACHE_ERROR_LOG--

                <Location />
                    Require all granted
                </Location>

                <Location /resources/>
                    ExpiresActive On
                    ExpiresDefault A900
                </Location>

                RewriteEngine On
                ProxyPreserveHost On
            </Macro>

            <VirtualHost *:80>
                Use --SETUP-VHOST_MACRO_NAME--
                ==SETUP-VHOST_REDIRECTION_RULES==
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

        vhost_http_to_https_redirection_rules = [
            (
                """
                RewriteCond %{REQUEST_URI} !^/.well-known"
                RewriteRule (.*) https://--SETUP-HOSTNAME--$1 [R=301]
                """,
                lambda cmd: True
            )
        ]

        vhost_ssl_private_key_file = None
        vhost_ssl_certificate_file = None

        vhost_ssl_template = """
            <VirtualHost *:443>
                Use --SETUP-VHOST_MACRO_NAME--
                ==SETUP-VHOST_REDIRECTION_RULES==

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
                lambda cmd: cmd.deployment_scheme not in (
                    "mod_wsgi",
                    "mod_wsgi_express"
                )
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
                    and cmd.deployment_scheme not in (
                        "mod_wsgi",
                        "mod_wsgi_express"
                    )
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
        first_commit_message = "Created the project."

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
                    """ % "\n".join(self.tasks),
                nargs = "+",
                metavar = "task",
                choices = list(self.tasks),
                default = self.tasks
            )

            self.add_argument(
                parser,
                "--skip-tasks",
                help = "Specific tasks to exclude.",
                nargs = "+",
                metavar = "task",
                choices = list(self.tasks),
                default = self.skipped_tasks,
                dest = "skipped_tasks"
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
                    """,
                default = self.workspace
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
                    """,
                default = self.dedicated_user
            )

            self.add_argument(
                parser.loc_group,
                "--dedicated-user-shell",
                help = """
                    If a dedicated system user for the project is created with
                    the --dedicated-user option, this parameter sets which
                    shell it should be assigned. Defaults to %s.
                    """ % self.dedicated_user_shell,
                default = self.dedicated_user_shell
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
                    The version of Woost that the website will be based on (the
                    name of a Mercurial branch, tag or bookmark). Defaults to '%s'
                    (the latest stable branch).
                    """ % self.woost_version,
                default = self.woost_version
            )

            self.add_argument(
                parser.cms_group,
                "--woost-dependency-specifier",
                help = """
                    The dependency specifier for the version of Woost that the
                    website should use. Defaults to '==VERSION.*', where
                    VERSION is the value given to the --woost-version
                    parameter.
                    """,
                default = self.woost_dependency_specifier
            )

            self.add_argument(
                parser.cms_group,
                "--cocktail-version",
                help = """
                    The version of Cocktail that the website will be based on.
                    If not set, the installer will attempt to automatically
                    select the version required by the selected Woost version.
                    """,
                default = self.cocktail_version
            )

            self.add_argument(
                parser.cms_group,
                "--extensions",
                nargs = "+",
                help = """
                    A list of Woost extension packages to install.%s
                    """ % (
                        ('Defaults to "%s"' % " ".join(self.extensions))
                        if self.extensions
                        else ""
                    ),
                default = self.extensions
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
                    mod_wsgi or mod_wsgi_express options are better for production
                    environments. The cherrypy option self hosts the application
                    using a single process; this can be a useful alternative if
                    installing apache / mod_wsgi is not possible or desirable.
                    Defaults to %s.
                    """ % (
                        self.deployment_scheme
                        or ", ".join(
                            "%s (%s)" % (defaults["deployment_scheme"], env)
                            for env, defaults in self.environments.items()
                        )
                    ),
                choices = [
                    "mod_rewrite",
                    "mod_wsgi",
                    "mod_wsgi_express",
                    "cherrypy"
                ],
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
                            for env, defaults in self.environments.items()
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

            self.add_boolean_argument(
                parser.db_group,
                "zeo_pack",
                "Enables or disables the packing of the ZODB database."
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

            self.add_boolean_argument(
                parser.purge_temp_files_group,
                "purge_temp_files",
                """
                Enables or disables the purging of the temporary files
                generated by the website (old sessions and file uploads).
                """
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

            self.add_boolean_argument(
                parser.backup_group,
                "backup",
                "Enables or disables backup operations."
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

            self.add_boolean_argument(
                parser.lets_encrypt_group,
                "lets_encrypt",
                """
                Enables or disables the automatic installation of a Lets
                Encrypt SSL certificate.
                """
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

            try:
                for task in self.preliminary_tasks:
                    getattr(self, task)()

                for task in self.tasks:
                    if task not in self.skipped_tasks:
                        getattr(self, task)()

            finally:
                if not self._is_child_process:
                    for task in self.cleanup_tasks:
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

        def apply_environment_presets(self):
            for setting, default in \
            self.environments[self.environment].items():
                if getattr(self, setting, None) is None:
                    setattr(self, setting, default)

        def init_config(self):

            if not self.source_repository and self.source_installation:
                self.source_repository = \
                    self.source_installation.rstrip("/") + "/" + self.website.lower()
                if (
                    ":" in self.source_repository
                    or "@" in self.source_repository
                ):
                    self.source_repository = (
                        "ssh://"
                        + self.source_repository.replace(":", "/", 1)
                    )

            if not self.woost_release_number:
                woost_release_number = self.woost_version
                if woost_release_number.endswith(".dev"):
                    woost_release_number = (
                        woost_release_number[:woost_release_number.rfind(".")]
                    )
                self.woost_release_number = woost_release_number

            if not self.woost_dependency_specifier:
                self.woost_dependency_specifier= \
                    "==%s.*" % self.woost_release_number

            if not self.alias:
                self.alias = self.website

            self.flat_website_name = self.website.lower().replace(".", "_")
            self.flat_website_alias = self.alias.lower().replace(".", "_")

            if self.workspace is None:
                if self.dedicated_user:
                    self.workspace = "/home/" + self.dedicated_user
                else:
                    self.workspace = os.environ.get("WORKSPACE")

                if not self.workspace:
                    sys.stderr.write(
                        "Must specify either --workspace or --dedicated-user, "
                        "or set the WORKSPACE environment variable.\n"
                    )
                    sys.exit(1)

            if not self.root_host:
                self.root_host = (
                    os.environ.get("WOOST_ROOT_HOST")
                    or self.default_root_host
                )

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
                "python%s" % python_version,
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
                self.hostname = self.flat_website_alias + "." + self.root_host

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

            # Cocktail
            if not self.cocktail_version:
                woost_version_parts = self.woost_release_number.split(".")
                while woost_version_parts:
                    try:
                        self.cocktail_version = self.cocktail_versions[
                            ".".join(woost_version_parts)
                        ]
                    except KeyError:
                        woost_version_parts.pop(-1)
                    else:
                        break

            if not self.cocktail_version:
                sys.stderr.write(
                    "Couldn't determine the required cocktail version. Check "
                    "your --woost-version parameter, or set --cocktail-version "
                    "manually\n"
                )
                sys.exit(1)

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
            elif self.deployment_scheme != "cherrypy":
                sys.stderr.write(
                    "Deployment with Apache requires Apache 2.4\n"
                )
                sys.exit(1)

            # Apache / mod_wsgi log files
            if self.dedicated_user:
                log_pattern = (
                    "/home/"
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
            if self.deployment_scheme in ("mod_wsgi", "mod_wsgi_express"):

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

                if self.deployment_scheme == "mod_wsgi_express":
                    self.mod_wsgi_express_service_name = self.alias + "-httpd"
                    self.mod_wsgi_express_root = os.path.join(
                        self.workspace
                            if self.dedicated_user
                            else self.root_dir,
                        "httpd"
                    )

                    if self.cache_enabled:
                        self.mod_wsgi_express_cacheserver_service_name = \
                            self.alias + "-cache-httpd"
                        self.mod_wsgi_express_cacheserver_root = os.path.join(
                            self.workspace
                                if self.dedicated_user
                                else self.root_dir,
                            "cache-httpd"
                        )

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

            self.launcher_terminal_tab_parameters = "\\\n\t".join(
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

        def update_features(self):

            updated_features = set()

            for feature in self.installer.features.values():
                if feature.installed_by_default:
                    feature.update()
                    updated_features.add(feature)

            for feature_id in self.get_required_features():
                if feature_id not in updated_features:
                    feature = self.installer.features[feature_id]
                    feature.update()
                    updated_features.add(feature)

        def get_required_features(self):

            yield "core3"

            if self.deployment_scheme != "cherrypy":
                yield "apache"

                if self.deployment_scheme == "mod_wsgi":
                    yield "modwsgi"
                elif self.deployment_scheme == "mod_wsgi_express":
                    yield "modwsgiexpress"

            if self.lets_encrypt:
                yield "letsencrypt"

            if (
                "install_libs" in self.tasks
                or "create_mercurial_repository" in self.tasks
            ):
                yield "mercurial"

            if self.launcher == "yes" or (
                self.launcher == "auto"
                and self.installer.features["launcher"].is_supported()
            ):
                yield "launcher"

        def expand_vars(self, string):
            return self.var_reg_expr.sub(self._inject_var, string)

        def process_template(self, string):
            string = self.installer.normalize_indent(string)
            string = self.expand_vars(string)
            return string

        def _inject_var(self, match):
            key = match.group("key").lower()
            try:
                return str(getattr(self, key))
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
                self._original_uid = os.geteuid()

                # Create the user, if necessary
                try:
                    user_info = getpwnam(self.dedicated_user)
                except KeyError:
                    is_new = True
                    self.installer._sudo(
                        self.useradd_script,
                        "-m", # Create the home directory
                        "-U", # Create a group for the user,
                        "-s", self.dedicated_user_shell, # Set the user's shell
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

        def pip_install(self, *args, **kwargs):
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "pip"
                ),
                "install",
                *args,
                **kwargs
            )

        def create_virtual_environment(self):

            self.installer.heading(
                "Creating the project's virtual environment"
            )

            # Make sure virtualenv is installed
            try:
                from virtualenv import create_environment
            except ImportError:
                self.installer._install_python_package("virtualenv")
                from virtualenv import create_environment

            # Remove the previous virtual environment
            env_preserved = False

            if any(
                os.path.exists(os.path.join(self.virtual_env_dir, subfolder))
                for subfolder in ("bin", "include", "lib", "local", "share")
            ):
                if not self.recreate_env:
                    self.installer.message("Preserving the existing environment")
                    env_preserved = True
                else:
                    self.installer.message("Deleting the current environment")

                    for dir in "bin", "include", "lib", "local", "share":
                        old_dir = os.path.join(self.virtual_env_dir, dir)
                        if os.path.exists(old_dir):
                            shutil.rmtree(old_dir)

            # Create the new virtual environment
            if not env_preserved:
                create_environment(self.virtual_env_dir)

            # Upgrade setuptools
            self.pip_install("--upgrade", "setuptools")

            # Upgrade pip
            self.pip_install("--upgrade", "pip")

            # Install ipython
            self.pip_install("ipython")

            # Create the custom environment activation script
            with open(self.project_env_script, "w") as f:
                f.write(self.process_template(self.project_env_template))

        def _hg(self, *args, **kwargs):

            # Mercurial has issues running under seteuid; fork to use setuid
            # instead
            if self.dedicated_user:
                euid = os.geteuid()
                os.seteuid(self._original_uid)
                pid = os.fork()
                if pid:
                    os.waitpid(pid, 0)
                    os.seteuid(euid)
                else:
                    self._is_child_process = True
                    os.setuid(euid)
                    self.installer._exec("hg", *args, **kwargs)
                    sys.exit(0)
            else:
                self.installer._exec("hg", *args, **kwargs)

        def install_libs(self):

            # TODO: Clone and setup PyStemmer with support for catalan

            # Clone and setup cocktail
            self.installer.heading("Installing cocktail")

            if not os.path.exists(
                os.path.join(self.cocktail_outer_dir, ".hg")
            ):
                self._hg(
                    "clone",
                    self.cocktail_repository,
                    self.cocktail_outer_dir,
                    "-u", self.cocktail_version
                )
            else:
                self._hg(
                    "pull",
                    cwd=self.cocktail_outer_dir
                )
                self._hg(
                    "update",
                    "--rev", self.cocktail_version,
                    cwd=self.cocktail_outer_dir
                )

            self.setup_python_package(self.cocktail_outer_dir)

            # Clone and setup woost
            self.installer.heading("Installing woost")

            if not os.path.exists(
                os.path.join(self.woost_outer_dir, ".hg")
            ):
                self._hg(
                    "clone",
                    self.woost_repository,
                    self.woost_outer_dir,
                    "-u", self.woost_version
                )
            else:
                self._hg(
                    "pull",
                    cwd = self.woost_outer_dir
                )
                self._hg(
                    "update",
                    "--rev", self.woost_version,
                    cwd = self.woost_outer_dir
                )

            self.setup_python_package(self.woost_outer_dir)

        def install_extensions(self):

            # Clone and setup woost extensions
            for ext in self.extensions:

                ext_parts = ext.split(":", 1)
                if len(ext_parts) == 2:
                    ext_name, ext_repository = ext_parts
                else:
                    ext_name = ext
                    ext_repository = (
                        self.default_extensions_repository
                        % ext_name
                    )

                self.installer.heading("Installing woost.extensions.%s" % ext_name)
                ext_dir = os.path.join(self.root_dir, "woost-" + ext_name)

                if not os.path.exists(ext_dir):
                    self._hg(
                        "clone",
                        ext_repository,
                        ext_dir
                    )
                else:
                    self._hg(
                        "pull", "-u",
                        cwd = ext_dir
                    )

                self.setup_python_package(ext_dir)

        def setup_python_package(self, package_root):
            self.installer._exec(
                self.python_bin,
                os.path.join(package_root, "setup.py"),
                "develop",
                "--find-links=" + self.python_packages_url,
                cwd = package_root
            )

        def create_project_skeleton(self):

            self.installer.heading("Creating the project skeleton")

            # Copy source code from an existing installation using mercurial
            if self.source_repository:
                if not os.path.exists(
                    os.path.join(self.project_outer_dir, ".hg")
                ):
                    clone_cmd = [
                        "clone",
                        self.source_repository,
                        self.project_outer_dir
                    ]
                    if self.revision:
                        clone_cmd += ["--rev", self.revision]
                    self._hg(*clone_cmd)
                else:
                    self._hg(
                        "pull",
                        cwd = self.project_outer_dir
                    )
                    update_cmd = ["update"]
                    if self.revision:
                        update_cmd += ["--rev", self.revision]
                    self._hg(
                        *update_cmd,
                        cwd = self.project_outer_dir
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
                        os.chmod(script, 0o774)

            # Create symbolic links to publish resource folders statically
            for link_name, source_dir in (
                (
                    "cocktail",
                    os.path.join(self.cocktail_dir, "html", "resources")
                ),
                (
                    "cocktail.ui",
                    os.path.join(self.cocktail_dir, "ui", "resources")
                ),
                (
                    "woost",
                    os.path.join(self.woost_dir, "views", "resources")
                ),
                (
                    "woost.admin.ui",
                    os.path.join(self.woost_dir, "admin", "ui", "resources")
                ),
                (
                    self.flat_website_name,
                    os.path.join(self.project_dir, "views", "resources")
                )
            ):
                target = os.path.join(self.static_dir, "resources", link_name)
                if not os.path.exists(target):
                    os.symlink(source_dir, target)

            # Write the setup file for the package
            with open(os.path.join(self.project_outer_dir, "setup.py"), "w") as f:
                setup_source = self.process_template(self.setup_template)
                f.write(setup_source)

            # Discard generated files that are managed with version control
            if self.source_repository:
                self._hg(
                    "revert", "--all", "--no-backup",
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

            # Copy the database file
            source_file = os.path.join(
                self.source_installation,
                self.website.lower(),
                *(
                    self.package.split(".")
                    + ["data", "database.fs"]
                )
            )
            dest_file = os.path.join(self.project_dir, "data", "database.fs")
            self.installer._exec(
                "rsync",
                "-P",
                "--update",
                source_file,
                dest_file
            )

            with self.zeo_process():

                # Apply migrations and change the hostname
                self._python(
                    """
                    import %s.settings
                    from cocktail.persistence import migrate, datastore
                    from woost import app
                    from woost.models import Website, extensions_manager
                    app.cache.enabled = False
                    extensions_manager.import_extensions()
                    migrate(True, True)
                    Website.select()[0].hosts[0] = "%s"
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

                if self.installation_id:
                    init_command.extend([
                        "--installation-id",
                        self.installation_id
                    ])

                if self.hostname:
                    init_command.extend(["--hostname", self.hostname])

                if self.lets_encrypt:
                    init_command.extend(["--https", "always"])

                if self.base_id:
                    init_command.extend(["--base-id", str(self.base_id)])

                self.installer._exec(*init_command)

        def copy_uploads(self):
            if self.source_installation:
                dest = os.path.join(self.project_dir, "upload")

                if self.uploads_repository:
                    self.installer.heading("Linking uploads")

                    for f in os.listdir(self.uploads_repository):
                        source_file = os.path.join(self.uploads_repository, f)
                        if os.path.isfile(source_file):
                            dest_file = os.path.join(dest, f)
                            if not os.path.exists(dest_file):
                                os.symlink(source_file, dest_file)
                else:
                    self.installer.heading("Copying uploads")
                    src = os.path.join(
                        self.source_installation,
                        self.website.lower(),
                        *(
                            self.package.split(".")
                            + ["upload"]
                        )
                    )
                    self.installer._exec(
                        "rsync",
                        "-r",
                        src.rstrip("/") + "/",
                        dest,
                        "--exclude", "temp"
                    )

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

                os.chmod(zeo_pack_script, 0o744)
                cronjob = "%s %s" % (self.zeo_pack_frequency, zeo_pack_script)
                self.installer._install_cronjob(cronjob, self.dedicated_user)

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

                os.chmod(purge_script, 0o744)
                cronjob = "%s %s" % (self.purge_temp_files_frequency, purge_script)
                self.installer._install_cronjob(cronjob, self.dedicated_user)

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

                os.chmod(backup_script, 0o744)
                cronjob = "%s %s" % (self.backup_frequency, backup_script)
                self.installer._install_cronjob(cronjob, self.dedicated_user)

        def obtain_lets_encrypt_certificate(self):
            if self.lets_encrypt:
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
                    os.chmod(self.mod_wsgi_daemon_python_eggs, 0o755)

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
            self.installer._sudo("service", "apache2", "reload")

        def get_apache_vhost_config(self, https = False):

            main_redirections = "".join(
                rule
                for rule, condition in self.vhost_redirection_rules
                if condition(self)
            )

            if https:
                http_redirections = "".join(
                    rule
                    for rule, condition
                    in self.vhost_http_to_https_redirection_rules
                    if condition(self)
                )
                https_redirections = main_redirections
            else:
                http_redirections = main_redirections

            template = self.apache_vhost_template.replace(
                "==SETUP-VHOST_REDIRECTION_RULES==",
                http_redirections
            )

            if https:
                template += self.vhost_ssl_template.replace(
                    "==SETUP-VHOST_REDIRECTION_RULES==",
                    https_redirections
                )

            if self.deployment_scheme == "mod_wsgi":
                template += "\n" + self.mod_wsgi_vhost_template

                if self.cache_enabled:
                    template += "\n" + self.cache_server_vhost_template

            return self.process_template(template)

        def configure_apache_https(self):

            if (
                self.vhost_ssl_private_key_file
                and self.vhost_ssl_certificate_file
            ):
                self.installer.heading(
                    "Configuring the site's Apache virtual host for HTTPS"
                )

                self.installer._sudo_write(
                    self.apache_vhost_file,
                    self.get_apache_vhost_config(https = True)
                )
                self.installer._sudo("service", "apache2", "reload")

        def configure_mod_wsgi_express(self):

            if self.deployment_scheme != "mod_wsgi_express":
                return

            self.installer.heading("Setting up a mod-wsgi-express server")
            self.pip_install("mod_wsgi")
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "mod_wsgi-express"
                ),
                "setup-server",
                os.path.join(
                    self.project_scripts_dir,
                    "wsgiapp.py"
                ),
                "--server-name",
                    self.hostname,
                "--allow-localhost",
                "--server-root",
                    self.mod_wsgi_express_root,
                "--working-directory",
                    self.static_dir,
                "--document-root",
                    self.static_dir,
                "--port",
                    str(self.port),
                "--user",
                    self.mod_wsgi_daemon_user,
                "--group",
                    self.mod_wsgi_daemon_user,
                "--processes",
                    str(self.mod_wsgi_daemon_processes),
                "--threads",
                    str(self.mod_wsgi_daemon_threads),
                "--python-path",
                    self.python_lib_path,
                "--maximum-requests",
                    str(self.mod_wsgi_daemon_maximum_requests),
                "--setup-only"
            )
            self.installer._create_service(
                self.mod_wsgi_express_service_name,
                self.process_template(self.mod_wsgi_express_service_template)
            )
            self.installer._start_service(self.mod_wsgi_express_service_name)

        def configure_mod_wsgi_express_cache_server(self):

            if not self.cache_enabled:
                return

            if self.deployment_scheme != "mod_wsgi_express":
                return

            self.installer.heading("Setting up a mod-wsgi-express cache server")
            self.pip_install("mod_wsgi")
            self.installer._exec(
                os.path.join(
                    self.virtual_env_dir,
                    "bin",
                    "mod_wsgi-express"
                ),
                "setup-server",
                os.path.join(
                    self.project_scripts_dir,
                    "cacheserver.py"
                ),
                "--server-name",
                    self.hostname,
                "--allow-localhost",
                "--server-root",
                    self.mod_wsgi_express_cacheserver_root,
                "--working-directory",
                    self.static_dir,
                "--document-root",
                    self.static_dir,
                "--port",
                    str(self.cache_server_port),
                "--user",
                    self.mod_wsgi_daemon_user,
                "--group",
                    self.mod_wsgi_daemon_user,
                "--processes",
                    "1",
                "--threads",
                    str(self.cache_server_threads),
                "--python-path",
                    self.python_lib_path,
                "--setup-only"
            )
            self.installer._create_service(
                self.mod_wsgi_express_cacheserver_service_name,
                self.process_template(
                    self.mod_wsgi_express_cacheserver_service_template
                )
            )
            self.installer._start_service(
                self.mod_wsgi_express_cacheserver_service_name
            )

        def create_mercurial_repository(self):

            if not self.mercurial:
                return

            if os.path.exists(os.path.join(self.project_outer_dir, ".hg")):
                return

            self.installer.heading(
                "Creating the project's mercurial repository"
            )

            # Initialize the repository
            self._hg("init", self.project_outer_dir)

            # Create an .hgignore file
            hg_ignore_path = os.path.join(self.project_outer_dir, ".hgignore")
            with open(hg_ignore_path, "w") as f:
                f.write(self.get_mercurial_ignore_file_contents())

            # Add files and make a first commit
            self._hg(
                "addremove",
                cwd = self.project_outer_dir
            )

            commit_command = [
                "commit", "-m",
                self.process_template(self.first_commit_message),
            ]

            if self.mercurial_user:
                commit_command.extend(["--user", self.mercurial_user])

            self._hg(*commit_command, cwd = self.project_outer_dir)

        def get_mercurial_ignore_file_contents(self):
            return "\n".join(
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

            if not self.installer.features["launcher"].is_supported():
                if self.launcher == "auto":
                    return
                else:
                    raise OSError(
                        "Can't install the desktop launcher without an X server"
                    )

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
            ]).decode("utf-8")

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
            ]).decode("utf-8").split()

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

            os.chmod(self.launcher_script, 0o774)

            with open(self.launcher_tab_script, "w") as f:
                f.write(self.process_template(self.launcher_tab_template))

            os.chmod(self.launcher_tab_script, 0o774)

            for key, cmd in self.launcher_tabs:
                tab_file_path = os.path.join(self.launcher_dir, "tab-" + key)
                with open(tab_file_path, "w") as tab_file:
                    cmd = self.process_template(cmd)
                    tab_file.write(cmd)
                os.chmod(tab_file_path, 0o774)

            # Desktop file
            desktop_file_path = os.path.dirname(self.desktop_file)
            self.installer._exec("mkdir", "-p", desktop_file_path)

            with open(self.desktop_file, "w") as f:
                f.write(self.process_template(self.desktop_file_template))
            os.chmod(self.desktop_file, 0o774)

            # Launcher icon
            from PIL import Image
            for icon_path in self.launcher_icons:
                icon_size_path = os.path.join(
                    os.path.expanduser("~"),
                    ".local",
                    "share",
                    "icons",
                    "hicolor",
                    "%dx%d" % Image.open(icon_path).size,
                    "apps"
                )
                if not os.path.exists(icon_size_path):
                    os.makedirs(icon_size_path)

                shutil.copy(
                    icon_path,
                    os.path.join(icon_size_path, self.alias + ".png")
                )

        def restore_original_user(self):
            if self._original_uid is not None:
                os.seteuid(self._original_uid)

    class MakeCommand(InstallCommand):

        name = "make"
        help = "Create or modify a Woost website."
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
                "--revision",
                help = """
                    Indicates the revision (or a branch, bookmark or tag) of
                    the project to clone.
                    """,
                default = self.revision
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
                help = """Don't copy uploaded files.""",
                action = "store_true"
            )

            self.add_argument(
                parser.copy_group,
                "--uploads-repository",
                help = u"Link file uploads from the given folder, instead of "
                       u"downloading them"
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

            for key, value in parameters.items():
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
                                write(base64.b64encode(chunk).decode("ascii"))
                            else:
                                write('"""')
                                break

                    write('\nif __name__ == "__main__":\n')
                    write("    installer = BundleInstaller()\n")

                    for default in self.bundle_defaults.items():
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
        self.unbundle.process_parameters(vars(args))
        self.unbundle()

    class UnbundleCommand(Installer.CopyCommand):
        name = "unbundle"

        preliminary_tasks = list(Installer.CopyCommand.preliminary_tasks)
        preliminary_tasks.append("extract_bundle_to_temp_dir")
        source_installation = "/tmp/woostproject-bundle"

        cleanup_tasks = list(Installer.CopyCommand.cleanup_tasks) + [
            "delete_bundle_temp_dir"
        ]

        # Warning: this must be a multiple of 4!
        chunk_size = 10920

        disabled_parameters = [
            "website",
            "source_installation"
        ]

        def extract_bundle_to_temp_dir(self):
            self.installer.heading("Extracting bundle data")
            os.makedirs(self.source_installation, exist_ok=True)
            os.chmod(self.source_installation, 0o755)
            self.extract_bundle_data(self.source_installation)

        def delete_bundle_temp_dir(self):
            self.installer.heading("Deleting temporary bundle data")
            shutil.rmtree(self.source_installation)

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
                    source_data = source_file.read()
                    target_data = self.processor(source_data)
                    with open(target, "w") as target_file:
                        target_file.write(target_data)


if __name__ == "__main__":
    Installer().run_cli()

