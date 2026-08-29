import os

from netbox.configuration_testing import *  # noqa: F403

PLUGINS = ["netbox_ssot"]
PLUGINS_CONFIG = {}

DATABASES["default"].update(  # noqa: F405
    {
        "NAME": os.getenv("NETBOX_SSOT_TEST_DB_NAME", DATABASES["default"]["NAME"]),  # noqa: F405
        "USER": os.getenv("NETBOX_SSOT_TEST_DB_USER", DATABASES["default"]["USER"]),  # noqa: F405
        "PASSWORD": os.getenv("NETBOX_SSOT_TEST_DB_PASSWORD", DATABASES["default"]["PASSWORD"]),  # noqa: F405
        "HOST": os.getenv("NETBOX_SSOT_TEST_DB_HOST", DATABASES["default"]["HOST"]),  # noqa: F405
        "PORT": os.getenv("NETBOX_SSOT_TEST_DB_PORT", DATABASES["default"]["PORT"]),  # noqa: F405
    }
)
