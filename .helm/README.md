# generic

![Version: 1.1.3](https://img.shields.io/badge/Version-1.1.3-informational?style=flat-square)

Generic helm chart for applications

**Homepage:** <https://git.severstal.severstalgroup.com/devops-public/helm-charts/templates/generic>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Albert Khaliullin | <af.khaliullin@serverstal.com> |  |
| Alexander Vidyaev | <aa.vidiaev@serverstal.com> |  |

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| autoscaling.additionalLabels | object | `{}` | Autooscaling additional labels |
| autoscaling.annotations | object | `{}` | Autoscaling annotations |
| autoscaling.enabled | bool | `false` | Enable autoscaling |
| autoscaling.maxReplicas | int | `10` | Max replicas sets the maximum number of replicas to scale |
| autoscaling.metrics | list | `[]` | Metrics is the list of metrics used for hpa |
| autoscaling.minReplicas | int | `1` | Min replicas sets the minimum number of replicas |
| cronjob.activeDeadlineSeconds | int | `""` | CronJob activeDeadlineSeconds |
| cronjob.additionalEnvFrom | object | `{}` | CronJob additional env. Mount env of any configMap or secret wich were created manually |
| cronjob.additionalLabels | object | `{}` | CronJob additional labels |
| cronjob.additionalMounts | object | `{}` | CronJob additional PVC. Mount PVC wich was created manually into container |
| cronjob.additionalPVC | list | `[]` | CronJob additional mounts. Mount configMap or secret wich were created manually as file into container |
| cronjob.affinity | object | `{}` | CronJob affinity |
| cronjob.annotations | object | `{}` | CronJob annotations |
| cronjob.backoffLimit | int | `""` | CronJob backoffLimit |
| cronjob.command | list | `["sh","-c","whoami"]` | CronJob command to run |
| cronjob.completions | int | `""` | CronJob completions |
| cronjob.containerSecurityContext | object | `{}` | CronJob container security context |
| cronjob.enabled | bool | `false` | Enable CronJob |
| cronjob.env | list | `[]` | Fixed Environment variable |
| cronjob.failedJobsHistoryLimit | int | `""` | CronJob failedJobsHistoryLimit |
| cronjob.imagePullPolicy | string | `"IfNotPresent"` | CronJob image pull policy. Overwrites global.imagepullpolicy |
| cronjob.nodeSelector | object | `{}` | CronJob node selector |
| cronjob.parallelism | int | `""` | CronJob parallelism |
| cronjob.resources | object | `{}` | CronJob resources |
| cronjob.restartPolicy | string | `"Never"` | CronJob restart policy |
| cronjob.schedule | string | `"* * * * *"` | CronJob schedule |
| cronjob.securityContext | object | `{}` | CronJob security context |
| cronjob.startingDeadlineSeconds | int | `""` | CronJob startingDeadlineSeconds |
| cronjob.successfulJobsHistoryLimit | int | `""` | CronJob successfulJobsHistoryLimit |
| cronjob.suspend | bool | `false` | CronJob suspend |
| cronjob.tolerations | list | `[]` | CronJob tolerations |
| cronjob.ttlSecondsAfterFinished | int | `""` | CronJob parallelism ttlSecondsAfterFinished. Working on Kubernetes >= v1.23 |
| deployment.additionalEnvFrom | object | `{}` | Deployment additional env. Mount env of any configMap or secret wich were created manually |
| deployment.additionalLabels | object | `{}` | Additional deployment labels |
| deployment.additionalMounts | object | `{}` | Deployment additional mounts. Mount configMap or secret wich were created manually as file into container |
| deployment.additionalPVC | list | `[]` | Deployment additional PVC. Mount PVC wich was created manually into container |
| deployment.affinity | object | `{}` | Deployment affinity |
| deployment.annotations | object | `{}` | Additional annotations for deployment |
| deployment.command | string | `""` | Command to run in main container |
| deployment.containerSecurityContext | object | `{}` | Container securityu context |
| deployment.env | list | `[]` | Fixed Environment variable |
| deployment.hostAliases | list | `[]` | Deployment host aliases |
| deployment.imagePullPolicy | string | `"IfNotPresent"` | Deployment image pull policy. Overwrites global.imagepullpolicy |
| deployment.initContainers | object | `{}` | Deployment init containers |
| deployment.nodeSelector | object | `{}` | Deployment node selector |
| deployment.podLabels | object | `{}` | Additional deployment pod labels |
| deployment.ports | list | `[]` | Deployment ports to expose |
| deployment.probes | object | `{}` | Deployment probes |
| deployment.reloadOnChange.enabled | bool | `true` | Reloads container if configMap or secret changed |
| deployment.replicas | int | `1` | Number of replicas for deployment |
| deployment.resources | object | `{}` | Deployment resources |
| deployment.securityContext | object | `{}` | Deployment security context |
| deployment.strategy.rollingUpdate | object | `{"maxSurge":"25%","maxUnavailable":"25%"}` | If strategy.type is `RollingUpdate` |
| deployment.strategy.type | string | `"RollingUpdate"` | Deployment strategy type |
| deployment.terminationGracePeriodSeconds | int | `60` | Default duration in seconds Kubernetes waits for container to exit before sending kill signal |
| deployment.tolerations | list | `[]` | Deployment tolerations |
| fullnameOverride | string | `""` | String to fully override common.names.fullname |
| global.image.repository | string | `"docker.repo.severstal.severstalgroup.com/nginx"` | Global container registry image path |
| global.image.tag | float | `1.13` | Global container registry image tag |
| global.imagePullPolicy | string | `"Always"` | Image pull policy |
| global.imagePullSecrets | string | `"container-registry"` | Container registry credentials |
| ingress[0].additionalLabels | object | `{}` | Additional ingress labels |
| ingress[0].annotations | object | `{}` | Ingress annotations |
| ingress[0].className | string | `"nginx"` | Ingress class name. Default template value is nginx |
| ingress[0].hosts | list | `[{"host":"generic-app.severstal.com","paths":[{"path":"/","port":8080}],"tls":{"common":true}}]` | Ingress hosts |
| ingress[0].hosts[0].tls | object | `{"common":true}` | Ingress SSL. Enabled or disabled |
| ingress[0].hosts[0].tls.common | bool | `true` | If `true`, *.kube.severstalgroup.severstal.com TLS will be created from `common/tls/` Vault path. If false, TLS must be in `projectNamespace/tls` or from customPath |
| job.activeDeadlineSeconds | int | `""` | Job activeDeadlineSeconds |
| job.additionalEnvFrom | object | `{}` | Job additional env. Mount env of any configMap or secret wich were created manually |
| job.additionalLabels | object | `{}` | Job additional labels |
| job.additionalMounts | object | `{}` | Job additional PVC. Mount PVC wich was created manually into container |
| job.additionalPVC | list | `[]` | Job additional mounts. Mount configMap or secret wich were created manually as file into container |
| job.affinity | object | `{}` | Job affinity |
| job.annotations | object | `{}` | Job annotations |
| job.backoffLimit | int | `""` | Job backoffLimit |
| job.command | list | `["sh","-c","whoami"]` | Job command to run |
| job.completions | int | `""` | Job completions |
| job.containerSecurityContext | object | `{}` | Job container security context |
| job.enabled | bool | `false` | Enable Job |
| job.env | list | `[]` | Fixed Environment variable |
| job.imagePullPolicy | string | `"IfNotPresent"` | Job image pull policy. Overwrites global.imagepullpolicy |
| job.nodeSelector | object | `{}` | Job node selector |
| job.parallelism | int | `""` | Job parallelism |
| job.resources | object | `{}` | Job resources |
| job.restartPolicy | string | `"Never"` | Job restart policy |
| job.securityContext | object | `{}` | Job security context |
| job.tolerations | list | `[]` | Job tolerations |
| job.ttlSecondsAfterFinished | int | `""` | Job parallelism ttlSecondsAfterFinished. Works on Kubernetes >= v1.23 |
| nameOverride | string | `""` | String to partially override fullname |
| networkPolicy.additionalLabels | string | `nil` | Network policy additional labels |
| networkPolicy.annotations | string | `nil` | Network policy annotations |
| networkPolicy.egress | list | `[]` | Network policy egress rules |
| networkPolicy.enabled | bool | `false` | Enable network policy |
| networkPolicy.ingress | list | `[]` | Network policy ingress rules |
| pdb.enabled | bool | `false` | Enable pod disruption budget. Works on Kubernetes >= v1.23 |
| pdb.minAvailable | int | `1` | Pod disruption budget configuration |
| podMonitor.enabled | bool | `false` | Create pod monitor |
| podMonitor.matchLabels | object | `{}` | Pod monitor custom match labels |
| podMonitor.monitorLabels | object | `{}` | Pod monitor custom monitor labels |
| podMonitor.namespaceSelector | list | `[]` | Pod moniutor namespace selector |
| podMonitor.path | string | `"/metrics"` | Pod monitor metrics path |
| podMonitor.port | string | Default value is service.ports[0].name | Pod monitor target port |
| prometheusRule.additionalLabels | object | `{}` | Prometheus rule additional labels |
| prometheusRule.enabled | bool | `false` | Create prometheus rule |
| prometheusRule.groups | list | `[]` | Prometheus rule rules |
| pvc | object | `{}` | Create PVC |
| rbac.enabled | bool | `false` | Enable role-based access control. If true, role & rolebinding will be created |
| rbac.role | object | `{}` |  |
| rbac.serviceAccount.additionalLabels | object | `{}` |  |
| rbac.serviceAccount.annotations | object | `{}` |  |
| rbac.serviceAccount.create | bool | `false` | Create service account. If true, role will be binded to this SA. If false, role will be binded to default SA |
| rbac.serviceAccount.name | string | `"serviceaccount"` |  |
| secrets.annotations | object | `{}` | Additional annotations |
| secrets.backend | string | `"eso-vault-backend"` | External secrets backend name |
| secrets.common | string | `""` | If `true`, creates a common secret for multiple services usage. Vault path must be projectNamespace/common/env/$env |
| secrets.commonFiles | object | `{}` | Mount common secret as file into container |
| secrets.enabled | bool | `true` | Use external secrets or not |
| secrets.env | bool | `true` | If `false`, disables providing secrets as env to provide only secrets as files |
| secrets.files | object | `{}` | Mount secret as file into container |
| secrets.kind | string | `"ClusterSecretStore"` | Kind of external secret backend |
| secrets.projectNamespace | string | `""` | Secret path in Vault secret backend |
| secrets.refreshInterval | string | `"30m"` | Refresh interval of secret |
| secrets.tls | object | `{"backend":"eso-vault-backend"}` | Provide ssl certificate with key via ESO |
| secrets.tls.backend | string | `"eso-vault-backend"` | External secrets backend name for TLS |
| service.additionalLabels | object | `{}` | Additional service labels |
| service.annotations | object | `{}` | Additional annotation |
| service.ports[0].name | string | `"http"` |  |
| service.ports[0].port | int | `8080` |  |
| service.ports[0].protocol | string | `"TCP"` |  |
| service.ports[0].targetPort | int | `8080` |  |
| service.type | string | `"ClusterIP"` | Service type |
| serviceMonitor.enabled | bool | `false` | Create service monitor |
| serviceMonitor.matchLabels | object | `{}` | Service monitor Custom match labels |
| serviceMonitor.monitorLabels | object | `{}` | Service monitor custom monitor labels |
| serviceMonitor.namespaceSelector | list | `[]` | Service monitor namespace selector |
| serviceMonitor.path | string | `"/metrics"` | Service monitor metrics path |
| serviceMonitor.port | string | `"http"` |  |
| autoscaling.enabled | bool | `false` | Master enable autoscaling |
| autoscaling.hpa.enabled | bool | `false` | Enable HorizontalPodAutoscaler |
| autoscaling.hpa.additionalLabels | object | `{}` | Additional labels for HPA |
| autoscaling.hpa.annotations | object | `{}` | Annotations HPA |
| autoscaling.hpa.targetRef.apiVersion | string | `"apps/v1"` | API version target contriller |
| autoscaling.hpa.targetRef.kind | string | `"Deployment"` | Kind target |
| autoscaling.hpa.targetRef.name | string | `""` | Name target-controller; Default fullname release |
| autoscaling.hpa.minReplicas | int | `null` | Minimum replicas; Default 1 |
| autoscaling.hpa.maxReplicas | int | `3` | Maximum (**requires** if HPA enabled) |
| autoscaling.hpa.metrics | list | `[]` | Metrics for HPA (Resource/Pods/External). |
| autoscaling.hpa.behavior | object | `{}` | behaviors scaleUp/scaleDown (only for `autoscaling/v2`) |
| autoscaling.vpa.enabled | bool | `false` | Enable VerticalPodAutoscaler |
| autoscaling.vpa.skipCRDCheck | bool | `false` | Skip check VPA CRD |
| autoscaling.vpa.name | string | `""` | Name VPA; Default fullname release |
| autoscaling.vpa.targetRef.apiVersion | string | `"apps/v1"` | API version target controller |
| autoscaling.vpa.targetRef.kind | string | `"Deployment"` | Kind target |
| autoscaling.vpa.targetRef.name | string | `""` | Name target-controller; By default fullname release |
| autoscaling.vpa.updatePolicy.updateMode | string | `"Off"` | Update VPA mode: `Off` (recommendation only) \| `Initial` \| `Auto` |
| autoscaling.vpa.resourcePolicy | object | `{}` | Resource Policy (containerPolicies, min/maxAllowed, controlledResources/Values) |
| autoscaling.vpa.recommenders | list | `[]` | User recommenders |
| autoscaling.enabled | bool | `false` | Master enable autoscaling |
| autoscaling.hpa.enabled | bool | `false` | Enable HorizontalPodAutoscaler |
| autoscaling.hpa.additionalLabels | object | `{}` | Additional labels for HPA |
| autoscaling.hpa.annotations | object | `{}` | Annotations HPA |
| autoscaling.hpa.targetRef.apiVersion | string | `"apps/v1"` | API version target contriller |
| autoscaling.hpa.targetRef.kind | string | `"Deployment"` | Kind target |
| autoscaling.hpa.targetRef.name | string | `""` | Name target-controller; Default fullname release |
| autoscaling.hpa.minReplicas | int | `null` | Minimum replicas; Default 1 |
| autoscaling.hpa.maxReplicas | int | `3` | Maximum (**requires** if HPA enabled) |
| autoscaling.hpa.metrics | list | `[]` | Metrics for HPA (Resource/Pods/External). |
| autoscaling.hpa.behavior | object | `{}` | behaviors scaleUp/scaleDown (only for `autoscaling/v2`) |
| autoscaling.vpa.enabled | bool | `false` | Enable VerticalPodAutoscaler |
| autoscaling.vpa.skipCRDCheck | bool | `false` | Skip check VPA CRD |
| autoscaling.vpa.name | string | `""` | Name VPA; Default fullname release |
| autoscaling.vpa.targetRef.apiVersion | string | `"apps/v1"` | API version target controller |
| autoscaling.vpa.targetRef.kind | string | `"Deployment"` | Kind target |
| autoscaling.vpa.targetRef.name | string | `""` | Name target-controller; By default fullname release |
| autoscaling.vpa.updatePolicy.updateMode | string | `"Off"` | Update VPA mode: `Off` (recommendation only) \| `Initial` \| `Auto` |
| autoscaling.vpa.resourcePolicy | object | `{}` | Resource Policy (containerPolicies, min/maxAllowed, controlledResources/Values) |
| autoscaling.vpa.recommenders | list | `[]` | User recommenders |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
