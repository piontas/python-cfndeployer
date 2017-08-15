

class CloudFormationException(Exception):
    msg = 'An unspecified Exception occurred'

    def __init__(self, **kwargs):
        msg = self.msg.format(**kwargs)
        Exception.__init__(self, msg)
        self.kwargs = kwargs


class EmptyStackName(CloudFormationException):
    msg = 'StackName is required!'


class TemplateNotSpecified(CloudFormationException):
    msg = 'TemplateBody or TemplateURL is required! {error}'


class TemplateValidationError(CloudFormationException):
    msg = 'Template is not valid! {error}'


class InvalidTemplatePathError(CloudFormationException):
    msg = 'Template File does not exist! {error}'


class InvalidTemplateUrlParameterError(CloudFormationException):
    msg = ('{property_name} parameter of {resource_id} resource is invalid. '
           'It must be a S3 URL or path to CloudFormation '
           'template file. Actual: {template_path}')


class EmptyChangeSet(CloudFormationException):
    msg = 'No changes to deploy. Stack {stack_name} is up to date'


class UpdateStackError(CloudFormationException):
    msg = 'No changes to update. Stack {stack_name} is up to date'


class StackDoesntExist(CloudFormationException):
    msg = 'Stack {stack_name} doesn\'t exist!'


class StackAlreadyExist(CloudFormationException):
    msg = 'Stack {stack_name} exist!'


class DeployFailed(CloudFormationException):
    msg = 'Failed to create/update the stack {stack_name}. {ex}'


class NoSuchBucketException(CloudFormationException):
    msg = 'S3 Bucket does not exist.'


class ExportException(CloudFormationException):
    msg = 'Unable to upload artifact {property_value} referenced by ' \
          '{property_name} parameter of {resource_id} resource.\n{ex}'
