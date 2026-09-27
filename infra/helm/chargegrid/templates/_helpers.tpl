{{- define "cg.name" -}}chargegrid{{- end -}}
{{- define "cg.labels" -}}
app.kubernetes.io/part-of: chargegrid
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
chargegrid/env: {{ .Values.env }}
{{- end -}}
{{- define "cg.backendImage" -}}
{{- if .Values.image.registry }}{{ .Values.image.registry }}/{{ end }}{{ .Values.image.backend }}:{{ required "image.tag is required" .Values.image.tag }}
{{- end -}}
{{- define "cg.frontendImage" -}}
{{- if .Values.image.registry }}{{ .Values.image.registry }}/{{ end }}{{ .Values.image.frontend }}:{{ required "image.tag is required" .Values.image.tag }}
{{- end -}}
{{- define "cg.securityContext" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
{{- end -}}
{{- define "cg.backendEnv" -}}
envFrom:
  - configMapRef: { name: chargegrid-env }
  - secretRef: { name: {{ .Values.secretName }} }
env:
  - { name: CONFIG_DIR, value: /config }
volumeMounts:
  - { name: config, mountPath: /config, readOnly: true }
  - { name: tmp, mountPath: /tmp }
{{- end -}}
{{- define "cg.backendVolumes" -}}
volumes:
  - name: config
    configMap: { name: {{ .Values.configMapName }} }
  - name: tmp
    emptyDir: {}
{{- end -}}
