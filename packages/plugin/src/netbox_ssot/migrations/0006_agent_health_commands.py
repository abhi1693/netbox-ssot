import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_ssot", "0005_source_agent_schedule"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="collectoragent",
            name="agent_version",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="collectoragent",
            name="protocol_version",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.CreateModel(
            name="AgentCommand",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[("test_connection", "Test connection"), ("run_now", "Run now")],
                        max_length=32,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("dispatched", "Dispatched"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.JSONField(default=dict)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="commands",
                        to="netbox_ssot.collectoragent",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="commands",
                        to="netbox_ssot.discoverysource",
                    ),
                ),
            ],
            options={"ordering": ("-requested_at",)},
        ),
        migrations.AddConstraint(
            model_name="agentcommand",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state__in", ("pending", "dispatched"))),
                fields=("source", "kind"),
                name="ssot_command_source_kind_active_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="agentcommand",
            index=models.Index(fields=["agent", "state", "requested_at"], name="ssot_command_agent_state_idx"),
        ),
    ]
