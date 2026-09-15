from rest_framework import serializers

from apps.core.project_models import Project
from .models import ReplicaEntry, ReplicaExtraction, ReplicaScope, ReplicaSource, ReplicaVersion
from .paths import config_hash, included, normalize_path


def scan_details(source, context):
    cache = context.setdefault('_replica_scan_details', {})
    if source.pk not in cache:
        scan = source.scans.first()
        details = None if scan is None else {
            'id': scan.id, 'status': scan.status, 'started_at': scan.started_at,
            'updated_at': scan.updated_at, 'completed_at': scan.completed_at,
            'entry_count': source.entries.filter(last_seen_scan=scan).count(),
        }
        state = 'discovery_only' if not source.included_paths else 'syncing' if source.active_run else (
            'unscanned' if scan is None or scan.config_hash != config_hash(source)
            else 'ready' if scan.status == 'completed' else 'incomplete'
        )
        cache[source.pk] = (details, state)
    return cache[source.pk]


class SourceSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)
    latest_scan = serializers.SerializerMethodField()
    scan_state = serializers.SerializerMethodField()

    class Meta:
        model = ReplicaSource
        fields = ['id', 'name', 'root_path', 'included_paths', 'excluded_paths', 'mode', 'enabled',
                  'max_file_size_mb', 'interval_seconds', 'last_heartbeat', 'last_success_at',
                  'last_error', 'status', 'created_at', 'active_run', 'latest_scan', 'scan_state']
        read_only_fields = ['id', 'last_heartbeat', 'last_success_at', 'last_error', 'created_at', 'active_run']

    def get_latest_scan(self, obj):
        return scan_details(obj, self.context)[0]

    def get_scan_state(self, obj):
        return scan_details(obj, self.context)[1]

    def validate_root_path(self, value):
        if not value.strip() or any(ord(c) < 32 for c in value) or '://' in value:
            raise serializers.ValidationError('Enter the UNC or absolute filesystem path used by the office connector.')
        if self.instance and value != self.instance.root_path and self.instance.scans.exists():
            raise serializers.ValidationError('Create a new source for a different server root to preserve existing file provenance.')
        return value

    def _paths(self, values):
        if not isinstance(values, list) or len(values) > 200:
            raise serializers.ValidationError('Provide up to 200 source-relative folder paths.')
        return list(dict.fromkeys(normalize_path(value) for value in values))

    def validate_included_paths(self, values):
        return self._paths(values)

    def validate_excluded_paths(self, values):
        return self._paths(values)

    def validate_max_file_size_mb(self, value):
        if not 1 <= value <= 1024:
            raise serializers.ValidationError('Choose a file size limit from 1 to 1024 MB.')
        return value

    def validate_interval_seconds(self, value):
        if not 60 <= value <= 86400:
            raise serializers.ValidationError('Choose an interval from 60 to 86400 seconds.')
        return value


class ScopeSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.filter(is_deleted=False), allow_null=True, required=False)
    project_code = serializers.CharField(source='project.code', read_only=True, default='')
    project_name = serializers.CharField(source='project.name', read_only=True, default='')
    source_mode = serializers.CharField(source='source.mode', read_only=True)
    source_status = serializers.CharField(source='source.status', read_only=True)
    source_last_heartbeat = serializers.DateTimeField(source='source.last_heartbeat', read_only=True)
    source_last_success_at = serializers.DateTimeField(source='source.last_success_at', read_only=True)
    source_scan_state = serializers.SerializerMethodField()
    in_inventory_scope = serializers.SerializerMethodField()

    class Meta:
        model = ReplicaScope
        fields = ['id', 'source', 'relative_path', 'project', 'project_code', 'project_name', 'access_enabled',
                  'source_mode', 'source_status', 'source_last_heartbeat', 'source_last_success_at',
                  'source_scan_state', 'in_inventory_scope']
        read_only_fields = ['id', 'source', 'relative_path']

    def get_source_scan_state(self, obj):
        return scan_details(obj.source, self.context)[1]

    def get_in_inventory_scope(self, obj):
        return included(obj.source, obj.relative_path)

    def validate(self, attrs):
        if self.instance and 'project' in attrs and attrs['project'] != self.instance.project:
            attrs.setdefault('access_enabled', False)
        project = attrs.get('project', self.instance.project if self.instance else None)
        enabled = attrs.get('access_enabled', self.instance.access_enabled if self.instance else False)
        if enabled and not project:
            raise serializers.ValidationError('Link a project before enabling access.')
        return attrs


class EntrySerializer(serializers.ModelSerializer):
    version_number = serializers.IntegerField(source='current_version.number', read_only=True, default=None)
    file_extension = serializers.SerializerMethodField()
    type_label = serializers.SerializerMethodField()

    class Meta:
        model = ReplicaEntry
        fields = ['id', 'source', 'scope', 'relative_path', 'parent_path', 'name', 'is_directory',
                  'size_bytes', 'modified_at', 'status', 'error', 'current_version', 'version_number',
                  'content_type', 'checksum', 'last_seen_at', 'file_extension', 'type_label']
        read_only_fields = fields

    def get_file_extension(self, obj):
        if obj.is_directory or '.' not in obj.name or obj.name.startswith('.') and obj.name.count('.') == 1:
            return ''
        return obj.name.rsplit('.', 1)[-1].lower()

    def get_type_label(self, obj):
        if obj.is_directory:
            return 'Folder'
        extension = self.get_file_extension(obj)
        return f'{extension.upper()} file' if extension else 'File (no extension)'


class VersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicaVersion
        fields = ['id', 'number', 'checksum', 'size_bytes', 'modified_at', 'created_at']


class ExtractionSerializer(serializers.ModelSerializer):
    version_number = serializers.IntegerField(source='version.number', read_only=True)
    stale = serializers.SerializerMethodField()

    class Meta:
        model = ReplicaExtraction
        fields = ['id', 'entry', 'version', 'version_number', 'status', 'sections', 'suggestions',
                  'warnings', 'error', 'created_at', 'reviewed_at', 'review_notes', 'stale']

    def get_stale(self, obj):
        return obj.entry.current_version_id != obj.version_id or obj.entry.status != 'available'


class InventorySerializer(serializers.Serializer):
    relative_path = serializers.CharField(max_length=2048)
    parent_path = serializers.CharField(max_length=2048, required=False, allow_blank=True)
    name = serializers.CharField(max_length=512, required=False)
    is_directory = serializers.BooleanField()
    size_bytes = serializers.IntegerField(min_value=0, default=0)
    modified_at = serializers.DateTimeField(allow_null=True, required=False, default=None)
    checksum = serializers.RegexField(r'^[a-fA-F0-9]{64}$', allow_blank=True, required=False, default='')
    error = serializers.CharField(max_length=2000, allow_blank=True, required=False, default='')

    def validate(self, attrs):
        path = normalize_path(attrs['relative_path'])
        attrs['relative_path'] = path
        attrs['name'] = path.rsplit('/', 1)[-1]
        attrs['parent_path'] = path.rpartition('/')[0]
        attrs['checksum'] = attrs['checksum'].lower()
        if len(path.split('/')[0]) > 1024 or len(attrs['name']) > 512:
            raise serializers.ValidationError('Folder or filename is too long.')
        if not attrs['is_directory'] and attrs['modified_at'] is None:
            raise serializers.ValidationError('File modification time is required.')
        return attrs
