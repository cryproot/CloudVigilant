import boto3
import json
from botocore.exceptions import ClientError
from datetime import datetime

class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return json.JSONEncoder.default(self, o)

def buscar_iam_usuarios():
    # Inicializar el cliente de IAM y CloudTrail
    iam_client = boto3.client('iam')
    cloudtrail_client = boto3.client('cloudtrail')

    # Lista de usuarios
    users = []
    try:
        paginator = iam_client.get_paginator('list_users')
        for page in paginator.paginate():
            users.extend(page['Users'])
    except ClientError as e:
        return json.dumps({"Error": f"Error al listar usuarios: {e.response['Error']['Code']}"})

    # Lista para almacenar los resultados
    resultados = []

    for user in users:
        user_name = user['UserName']
        is_admin = False
        mfa_enabled = False
        has_console_access = False
        last_used = None
        access_keys = []
        nist_compliance = None

        # Obtener las políticas de usuario
        try:
            user_policies = iam_client.list_attached_user_policies(UserName=user_name)
            policies = user_policies['AttachedPolicies']
            
            for policy in policies:
                policy_arn = policy['PolicyArn']

                # Verificar si la política es la política de administrador
                if policy_arn == 'arn:aws:iam::aws:policy/AdministratorAccess':
                    is_admin = True
                    break

            if not is_admin:
                # Obtener las políticas de grupo
                user_groups = iam_client.list_groups_for_user(UserName=user_name)
                groups = user_groups['Groups']
                
                for group in groups:
                    group_name = group['GroupName']
                    group_policies = iam_client.list_attached_group_policies(GroupName=group_name)
                    group_attached_policies = group_policies['AttachedPolicies']
                    
                    for group_policy in group_attached_policies:
                        if group_policy['PolicyArn'] == 'arn:aws:iam::aws:policy/AdministratorAccess':
                            is_admin = True
                            break
                    
                    if is_admin:
                        break

            # Verificar si el usuario tiene MFA habilitado
            mfa_devices = iam_client.list_mfa_devices(UserName=user_name)
            if mfa_devices['MFADevices']:
                mfa_enabled = True

            # Verificar si el usuario tiene acceso a la consola de AWS
            try:
                iam_client.get_login_profile(UserName=user_name)
                has_console_access = True
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    has_console_access = False
                else:
                    raise e

            # Evaluar el cumplimiento NIST
            if not mfa_enabled and has_console_access:
                nist_compliance = "Fail"
            elif not mfa_enabled and not has_console_access:
                nist_compliance = "True"
            else:
                nist_compliance = "True"

            # Obtener las claves de acceso del usuario
            try:
                access_keys_response = iam_client.list_access_keys(UserName=user_name)
                access_keys = access_keys_response['AccessKeyMetadata']

            except ClientError as e:
                resultados.append({
                    'user_name': user_name,
                    'is_admin': is_admin,
                    'mfa_enabled': mfa_enabled,
                    'has_console_access': has_console_access,
                    'last_used': str(last_used) if last_used else None,
                    'access_keys': 'Error obteniendo las claves de acceso',
                    'nist_compliance': nist_compliance
                })
                continue

            # Obtener la última fecha de acceso usando AWS CloudTrail
            try:
                events = cloudtrail_client.lookup_events(
                    LookupAttributes=[
                        {
                            'AttributeKey': 'Username',
                            'AttributeValue': user_name
                        },
                    ],
                    MaxResults=1
                )
                if events['Events']:
                    last_used = events['Events'][0]['EventTime']
                else:
                    last_used = None

            except ClientError as e:
                # Manejar errores al obtener detalles de último acceso
                resultados.append({
                    'user_name': user_name,
                    'is_admin': is_admin,
                    'mfa_enabled': mfa_enabled,
                    'has_console_access': has_console_access,
                    'last_used': 'Error obteniendo la última fecha de acceso',
                    'access_keys': access_keys,
                    'nist_compliance': nist_compliance
                })
                continue

        except ClientError as e:
            resultados.append({
                'user_name': user_name,
                'is_admin': f'Error: {e.response["Error"]["Code"]}',
                'mfa_enabled': 'Error',
                'has_console_access': 'Error',
                'last_used': 'Error',
                'access_keys': 'Error',
                'nist_compliance': 'Error'
            })
            continue

        # Almacenar resultados del usuario actual
        resultados.append({
            'user_name': user_name,
            'is_admin': is_admin,
            'mfa_enabled': mfa_enabled,
            'has_console_access': has_console_access,
            'last_used': last_used,
            'access_keys': access_keys,
            'nist_compliance': nist_compliance
        })

    # Devolver resultados en formato JSON usando el encoder personalizado
    return json.dumps(resultados, cls=DateTimeEncoder)
