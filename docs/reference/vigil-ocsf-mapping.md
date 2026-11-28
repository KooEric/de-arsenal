# Vigil `events` 컬럼과 OCSF 필드

Vigil의 `events`는 OCSF를 구현하지 않는다. 탐지 룰에 필요한 컬럼만 평평하게 둔다. 아래 표는 나중에
OCSF(API Activity, class 6003)로 옮길 때 어느 필드에 해당하는지 적은 것이다.

| `events` 컬럼 | 타입 | CloudTrail 원본 | OCSF 필드 |
|---|---|---|---|
| `event_id` | varchar | `eventID` | `metadata.uid` |
| `event_time` | timestamp (UTC) | `eventTime` | `time` |
| `source` | varchar | 상수 `cloudtrail` | `metadata.product.name` |
| `service` | varchar | `eventSource` | `api.service.name` |
| `action` | varchar | `eventName` | `api.operation` |
| `actor_type` | varchar | `userIdentity.type` | `actor.user.type` |
| `actor_id` | varchar | `userIdentity.arn` | `actor.user.uid` |
| `src_ip` | varchar | `sourceIPAddress` | `src_endpoint.ip` |
| `region` | varchar | `awsRegion` | `cloud.region` |
| `outcome` | varchar | `errorCode` 유무, 콘솔 로그인은 `responseElements.ConsoleLogin` | `status` |
| `mfa_used` | boolean | `additionalEventData.MFAUsed` | `actor.session.is_mfa` |
| `resource` | varchar | `requestParameters`의 `bucketName`, `userName`, `name` 중 처음 있는 값 | `resources[].name` |
| `raw` | varchar (JSON) | 이벤트 전체 | `raw_data` |

OCSF 필드명은 https://schema.ocsf.io/ 의 API Activity 클래스 기준이다.
