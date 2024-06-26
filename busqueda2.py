import boto3
import json
from botocore.exceptions import ClientError

def buscar_ec2_configuraciones():
    ec2_client = boto3.client('ec2')

    response = ec2_client.describe_instances()
    reservations = response['Reservations']

    resultados = []

    for reservation in reservations:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            instance_name = ''

            for tag in instance.get('Tags', []):
                if tag['Key'] == 'Name':
                    instance_name = tag['Value']
                    break

            # Initialize configuration variables
            imdsv2_status = 'Unknown'
            ssm_status = 'Unknown'
            monitoring_status = 'Unknown'
            private_ip = instance.get('PrivateIpAddress', 'N/A')
            public_ip = instance.get('PublicIpAddress', 'N/A')

            # Check IMDSv2 (Instance Metadata Service Version 2)
            try:
                instance_metadata_options = ec2_client.describe_instances(InstanceIds=[instance_id])
                metadata_options = instance_metadata_options['Reservations'][0]['Instances'][0].get('MetadataOptions', {})
                http_tokens = metadata_options.get('HttpTokens', 'Unknown')
                if http_tokens == 'required':
                    imdsv2_status = 'IMDSv2 required'
                elif http_tokens == 'optional':
                    imdsv2_status = 'IMDSv2 optional'
                else:
                    imdsv2_status = 'IMDSv2 disabled or not required'
            except ClientError as e:
                if e.response['Error']['Code'] == 'InvalidParameterValue':
                    imdsv2_status = 'IMDSv2 attribute not available'
                else:
                    imdsv2_status = f'IMDSv2 Error: {e.response["Error"]["Code"]}'

            # Check if managed by Systems Manager
            try:
                ssm_client = boto3.client('ssm')
                response = ssm_client.describe_instance_information(
                    InstanceInformationFilterList=[
                        {
                            'key': 'InstanceIds',
                            'valueSet': [instance_id]
                        }
                    ]
                )
                if not response['InstanceInformationList']:
                    ssm_status = 'Not managed by Systems Manager'
                else:
                    ssm_status = 'Managed by Systems Manager'
            except ClientError as e:
                ssm_status = f'Systems Manager Error: {e.response["Error"]["Code"]}'

            # Check if detailed monitoring is enabled
            try:
                monitoring_response = ec2_client.describe_instances(InstanceIds=[instance_id])
                monitoring_state = monitoring_response['Reservations'][0]['Instances'][0]['Monitoring']['State']
                if monitoring_state == 'disabled':
                    monitoring_status = 'Detailed monitoring not enabled'
                elif monitoring_state == 'enabled':
                    monitoring_status = 'Detailed monitoring enabled'
                else:
                    monitoring_status = 'Unknown monitoring state'
            except ClientError as e:
                monitoring_status = f'Detailed monitoring status error: {e.response["Error"]["Message"]}'

            # Get VolumeId and SnapshotId information from describe-volumes and describe-snapshots
            volume_snapshot_pairs = []
            try:
                volumes_response = ec2_client.describe_volumes(Filters=[{'Name': 'attachment.instance-id', 'Values': [instance_id]}])
                for volume in volumes_response['Volumes']:
                    volume_id = volume['VolumeId']
                    encrypted = volume['Encrypted'] if 'Encrypted' in volume else False
                    snapshot_ids_pt1 = []  # For describe-snapshots
                    snapshot_ids_pt2 = []  # For describe-volumes

                    # Obtain snapshot IDs from describe-snapshots
                    try:
                        snapshots_response = ec2_client.describe_snapshots(Filters=[{'Name': 'volume-id', 'Values': [volume_id]}])
                        for snapshot in snapshots_response['Snapshots']:
                            snapshot_ids_pt1.append(snapshot['SnapshotId'])
                    except ClientError as e:
                        snapshot_ids_pt1.append(f'Snapshot Error: {e.response["Error"]["Code"]}')

                    # Obtain snapshot IDs from describe-volumes
                    try:
                        volume_details = ec2_client.describe_volumes(VolumeIds=[volume_id])
                        for volume_detail in volume_details['Volumes']:
                            if 'SnapshotId' in volume_detail:
                                snapshot_id_pt2 = volume_detail['SnapshotId']
                                snapshot_id_pt2_encrypted = volume_detail.get('Encrypted', False)
                                snapshot_ids_pt2.append({
                                    'SnapshotId': snapshot_id_pt2,
                                    'Encrypted': snapshot_id_pt2_encrypted
                                })
                    except ClientError as e:
                        snapshot_ids_pt2.append({
                            'SnapshotId': f'Snapshot Error: {e.response["Error"]["Code"]}',
                            'Encrypted': False
                        })

                    volume_snapshot_pairs.append({
                        'VolumeId': volume_id,
                        'Encrypted': encrypted,
                        'SnapshotIds_pt1': snapshot_ids_pt1 if snapshot_ids_pt1 else ['No Snapshots from describe-snapshots'],
                        'SnapshotIds_pt2': snapshot_ids_pt2 if snapshot_ids_pt2 else [{'SnapshotId': 'No Snapshots from describe-volumes', 'Encrypted': False}]
                    })
            except ClientError as e:
                volume_snapshot_pairs.append({
                    'VolumeId': f'Volume Error: {e.response["Error"]["Code"]}',
                    'Encrypted': False,
                    'SnapshotIds_pt1': ['N/A'],
                    'SnapshotIds_pt2': [{'SnapshotId': 'N/A', 'Encrypted': False}]
                })

            for pair in volume_snapshot_pairs:
                for snapshot_pt2 in pair['SnapshotIds_pt2']:
                    resultados.append({
                        'instance_id': instance_id,
                        'instance_name': instance_name,
                        'imdsv2_status': imdsv2_status,
                        'ssm_status': ssm_status,
                        'monitoring_status': monitoring_status,
                        'private_ip': private_ip,
                        'public_ip': public_ip,
                        'volume_id': pair['VolumeId'],
                        'snapshot_id_pt1': pair['SnapshotIds_pt1'][0] if pair['SnapshotIds_pt1'] else 'N/A',
                        'snapshot_id_pt2': snapshot_pt2['SnapshotId'],
                        'snapshot_id_pt2_encrypted': snapshot_pt2['Encrypted'],
                        'encrypted': pair['Encrypted']
                    })

    return json.dumps(resultados)
