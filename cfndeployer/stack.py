import os
import time

from boto3 import Session
from botocore.exceptions import ValidationError, ClientError, \
    ParamValidationError, WaiterError

from .exceptions import EmptyStackName, DeployFailed, TemplateNotSpecified, \
    TemplateValidationError, EmptyChangeSet, UpdateStackError, \
    StackDoesntExist, StackAlreadyExist


CFN_KWARGS = (
        'StackName', 'TemplateBody', 'TemplateURL', 'Parameters',
        'NotificationARNs', 'Capabilities', 'ResourceTypes', 'RoleARN',
        'StackPolicyBody', 'StackPolicyURL', 'Tags', 'ClientRequestToken'
    )


class Stack:
    KWARGS = CFN_KWARGS

    CREATE_KWARGS = KWARGS + (
        'DisableRollback', 'TimeoutInMinutes', 'OnFailure'
    )

    UPDATE_KWARGS = KWARGS + (
        'UsePreviousTemplate', 'StackPolicyDuringUpdateBody',
        'StackPolicyDuringUpdateURL',
    )

    DELETE_KWARGS = (
        'StackName', 'RetainResources', 'RoleARN', 'ClientRequestToken'
    )

    DESCRIBE_STACKS = ('StackName', 'NextToken')

    CREATE_CHANGE_SET_KWARGS = KWARGS + (
        'UsePreviousTemplate', 'ChangeSetName', 'ClientToken',
        'Description', 'ChangeSetType')

    EXECUTE_CHANGE_SET = ('ChangeSetName', 'StackName', 'ClientRequestToken')

    CHANGE_SET_PREFIX = 'stack-change-set-'

    WAITER_DELAY = 5

    WAIT_KWARGS = DESCRIBE_STACKS
    WAIT_CHANGE_SET_KWARGS = DESCRIBE_STACKS + ('ChangeSetName',)

    def __init__(self, **kwargs):
        """
        Creates customized specific initial state.
        :param kwargs:
        """
        if 'StackName' not in kwargs:
            raise EmptyStackName

        self.kwargs = kwargs
        self._client = Session(profile_name=kwargs.get('Profile', None)).client(
            'cloudformation')

    def _prepare_kwargs(self, kwargs_list):
        """
        Prepares kwargs based on allowed ones from kwargs_list.
        :param kwargs_list: list of allowed kwargs.
        :return: Filtered kwargs.
        """
        return {key: self.kwargs[key] for key in self.kwargs if key in
                getattr(self, kwargs_list)}

    def _validate_template(self):
        """
        Validates CloudFormation template. It can be file, url or template body.
        :return:
        """
        try:
            if 'TemplateBody' in self.kwargs:
                if os.path.isfile(self.kwargs['TemplateBody']):
                    with open(self.kwargs['TemplateBody'], 'r') as body:
                        template_body = body.read()
                    self.kwargs['TemplateBody'] = template_body
                self._client.validate_template(
                    TemplateBody=self.kwargs['TemplateBody'])
            elif 'TemplateURL' in self.kwargs:
                self._client.validate_template(
                    TemplateURL=self.kwargs['TemplateURL'])
            else:
                raise KeyError

        except KeyError as e:
            raise TemplateNotSpecified(error=e)

        except (ValidationError, ClientError, ParamValidationError) as e:
            raise TemplateValidationError(error=e)

    def _describe_stack(self):
        """
        Describes CloudFormation Stack.
        :return: Returns Stack information if Stack exists. False otherwise.
        """
        try:
            stacks = self._client.describe_stacks(
                **self._prepare_kwargs('DESCRIBE_STACKS'))
            return stacks['Stacks'][0]

        except ClientError as ex:
            if 'Stack with id {0} does not exist'.format(
                    self.kwargs['StackName']) in str(ex):
                return False
            else:
                raise ex

    def _stack_exists(self):
        """
        Checks if a CloudFormation stack with given name exists.
        :return: True if Stack exists. False otherwise.
        """
        stack = self._describe_stack()
        if not stack:
            return False

        return stack['StackStatus'] != 'REVIEW_IN_PROGRESS'

    def _create_change_set(self):
        """
        Creates CloudFormation Change Set.
        :return:
        """
        if 'ChangeSetName' not in self.kwargs:
            self.kwargs['ChangeSetName'] = self.CHANGE_SET_PREFIX + \
                                           str(int(time.time()))

        self.kwargs['ChangeSetType'] = 'UPDATE'
        if not self._stack_exists():
            self.kwargs['ChangeSetType'] = 'CREATE'

        try:
            self._client.create_change_set(**self._prepare_kwargs(
                'CREATE_CHANGE_SET_KWARGS'))
        except Exception as e:
            raise e

    def _wait_for_stack(self, waiter):
        """
        Waits for CloudFormation action to be completed.
        :param waiter: create, update, delete.
        :return:
        """
        waiter = self._client.get_waiter('stack_{0}_complete'.format(waiter))
        waiter.config.delay = self.WAITER_DELAY
        try:
            waiter.wait(**self._prepare_kwargs('WAIT_KWARGS'))
        except WaiterError as ex:
            raise ex

    def _wait_for_change_set(self):
        """
        Waits for CloudFormation Change Set to be created.
        :return:
        """
        waiter = self._client.get_waiter('change_set_create_complete')
        waiter.config.delay = self.WAITER_DELAY
        try:
            waiter.wait(**self._prepare_kwargs('WAIT_CHANGE_SET_KWARGS'))
        except WaiterError as e:
            resp = e.last_response
            status = resp['Status']
            reason = resp['StatusReason']
            msg = ('No updates are to be performed',
                   'The submitted information didn\'t contain changes.')
            if status == 'FAILED' and (msg[0] or msg[1] not in reason):
                raise EmptyChangeSet(stack_name=self.kwargs['StackName'])
            raise e

    def _execute_change_set(self):
        """
        Executes CloudFormation Change Set.
        :return:
        """
        return self._client.execute_change_set(**self._prepare_kwargs(
            'EXECUTE_CHANGE_SET'))

    def _wait_for_execute(self):
        """
        Wait for Cloud Formation Change Set to be executed.
        :return:
        """
        change_set_type = self.kwargs['ChangeSetType']

        if change_set_type == 'CREATE':
            waiter = self._client.get_waiter('stack_create_complete')
        elif change_set_type == 'UPDATE':
            waiter = self._client.get_waiter('stack_update_complete')

        try:
            waiter.wait(**self._prepare_kwargs('DESCRIBE_STACKS'))
        except WaiterError as ex:
            raise DeployFailed(stack_name=self.kwargs['StackName'], ex=ex)

    def _update_stack(self):
        """
        Updates CloudFormation Stack.
        :return:
        """
        if not self._stack_exists():
            raise StackDoesntExist(stack_name=self.kwargs['StackName'])
        try:
            self._client.update_stack(**self._prepare_kwargs('UPDATE_KWARGS'))
        except ClientError as ex:
            if 'No updates are to be performed.' in str(ex):
                raise UpdateStackError(stack_name=self.kwargs['StackName'])
            raise ClientError

    def _create_stack(self):
        """
        Creates CloudFormation Stack.
        :return:
        """
        if self._stack_exists():
            raise StackAlreadyExist(stack_name=self.kwargs['StackName'])
        self._client.create_stack(**self._prepare_kwargs('CREATE_KWARGS'))

    def _delete_stack(self):
        """
        Deletes CloudFormation Stack.
        :return:
        """
        if not self._stack_exists():
            raise StackDoesntExist(stack_name=self.kwargs['StackName'])
        self._client.delete_stack(**self._prepare_kwargs('DELETE_KWARGS'))

    def deploy(self, execute_change_set=True):
        """
        Method creates CloudFormation Stack Change Set and executes Change Set.
        It validates Template first.
        :param execute_change_set: If True Change Set will be executed.
        :return:
        """
        self._validate_template()
        self._create_change_set()
        self._wait_for_change_set()

        print('\nDeploying CF Stack...')
        if execute_change_set:
            self._execute_change_set()
            self._wait_for_execute()
        return 0

    def create(self):
        """
        Metohd creates CloudFormation Stack.
        :return:
        """
        self._validate_template()

        print('Creating CF Stack...')
        self._create_stack()
        self._wait_for_stack('create')
        return 0

    def update(self):
        """
        Metohd updates CloudFormation Stack.
        :return:
        """
        self._validate_template()

        print('Updating CF Stack...')
        self._update_stack()
        self._wait_for_stack('update')
        return 0

    def delete(self):
        """
        Metohd deletes CloudFormation Stack.
        :return:
        """

        print('Deleting CF Stack...')
        self._delete_stack()
        self._wait_for_stack('delete')
        return 0
