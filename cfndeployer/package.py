import json
import os
import sys
import errno
import uuid
import hashlib
import yaml
import threading

from boto3 import Session
from boto3.s3.transfer import TransferManager, BaseSubscriber
from botocore.client import Config
from botocore.exceptions import ClientError

from .exceptions import TemplateNotSpecified, InvalidTemplatePathError, \
    NoSuchBucketException
from .template import Template


class ProgressPercentage(BaseSubscriber):
    # This class was copied directly from S3Transfer docs
    def __init__(self, filename, remote_path):
        self._filename = filename
        self._remote_path = remote_path
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def on_progress(self, future, bytes_transferred, **kwargs):

        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_transferred
            percentage = (self._seen_so_far / self._size) * 100
            sys.stdout.write(
                    "\rUploading to %s  %s / %s  (%.2f%%)" %
                    (self._remote_path, self._seen_so_far,
                     self._size, percentage))
            sys.stdout.flush()


class S3Uploader(object):
    """
    Class to upload objects to S3 bucket that use versioning. If bucket
    does not already use versioning, this class will turn on versioning.
    Modified from https://github.com/aws/aws-cli/blob/develop/awscli/
    customizations/cloudformation/s3uploader.py
    """

    def __init__(self, s3_client, bucket_name, region, prefix=None,
                 kms_key_id=None, force_upload=False):
        self.bucket_name = bucket_name
        self.prefix = prefix
        self.kms_key_id = kms_key_id or None
        self.force_upload = force_upload
        self.s3 = s3_client
        self.region = region
        self.transfer_manager = TransferManager(self.s3)

    def upload(self, filename, remote_path):
        """
        Uploads given file to S3
        :param file_name: Path to the file that will be uploaded
        :param remote_path:  be uploaded
        :return: VersionId of the latest upload
        """

        if self.prefix and len(self.prefix) > 0:
            remote_path = '{}/{}'.format(self.prefix, remote_path)

        # Check if a file with same data exists
        if not self.force_upload and self.file_exists(remote_path):
            return self.make_url(remote_path)

        try:

            # Default to regular server-side encryption unless customer has
            # specified their own KMS keys
            additional_args = {
                'ServerSideEncryption': 'AES256'
            }

            if self.kms_key_id:
                additional_args['ServerSideEncryption'] = 'aws:kms'
                additional_args['SSEKMSKeyId'] = self.kms_key_id

            progress_procentage = ProgressPercentage(filename, remote_path)
            self.transfer_manager.upload(
                filename, self.bucket_name, remote_path, additional_args,
                [progress_procentage]).result()

            return self.make_url(remote_path)

        except ClientError as ex:
            error_code = ex.response['Error']['Code']
            if error_code == 'NoSuchBucket':
                raise NoSuchBucketException(
                        bucket_name=self.bucket_name)
            raise ex

    def upload_with_dedup(self, file_name, extension=None):
        """
        Makes and returns name of the S3 object based on the file's MD5 sum

        :param file_name: file to upload
        :param extension: String of file extension to append to the object
        :return: S3 URL of the uploaded object
        """

        filemd5 = self.file_checksum(file_name)
        remote_path = filemd5
        if extension:
            remote_path = '{}.{}'.format(remote_path,extension)

        return self.upload(file_name, remote_path)

    def file_exists(self, remote_path):
        """
        Check if the file we are trying to upload already exists in S3

        :param remote_path:
        :return: True, if file exists. False, otherwise
        """

        try:
            self.s3.head_object(Bucket=self.bucket_name, Key=remote_path)
            return True
        except ClientError:
            return False

    def make_url(self, obj_path):
        return 's3://{}/{}'.format(self.bucket_name, obj_path)

    @staticmethod
    def file_checksum(file_name):

        with open(file_name, 'rb') as file_handle:
            md5 = hashlib.md5()
            # Read file in chunks of 4096 bytes
            block_size = 4096

            # Save current cursor position and reset cursor to start of file
            curpos = file_handle.tell()
            file_handle.seek(0)

            buf = file_handle.read(block_size)
            while len(buf) > 0:
                md5.update(buf)
                buf = file_handle.read(block_size)

            # Restore file cursor's position
            file_handle.seek(curpos)

            return md5.hexdigest()

    def to_path_style_s3_url(self, key, version=None):
        """
            This link describes the format of Path Style URLs
            http://docs.aws.amazon.com/AmazonS3/latest/dev/UsingBucket.html
        """
        base = 'https://s3.amazonaws.com'
        if self.region and self.region != 'us-east-1':
            base = 'https://s3-{}.amazonaws.com'.format(self.region)

        result = '{0}/{1}/{2}'.format(base, self.bucket_name, key)
        if version:
            result = '{0}?versionId={1}'.format(result, version)

        return result


class Package:
    def __init__(self, **kwargs):
        """
        Creates customized specific initial state.
        :param kwargs:
        """
        self.kwargs = kwargs

        if 'TemplateFile' not in self.kwargs:
            raise TemplateNotSpecified

        self.template_file = self.kwargs['TemplateFile']
        if not os.path.isfile(self.template_file):
            raise InvalidTemplatePathError

        client = Session(profile_name=kwargs.get('Profile', None)).client(
            's3', config=Config(signature_version='s3v4'), verify=True)

        bucket = self.kwargs.get('Bucket', 'cfndeployer-{}'.format(
            str(uuid.uuid4())))

        self.s3_uploader = S3Uploader(
            client, bucket, self.kwargs.get('Region', 'eu-west-1'),
            self.kwargs.get('StackName', 'cfn'), None, True)

    def package(self, use_json=False):
        """
        Cloudformation package - sends packaged file to s3
        :return:
        """
        output_file = self.kwargs.get('OutputFile', 'template.package')
        exported_str = self._export(self.template_file, use_json)

        self._write_output(output_file, exported_str)
        return output_file

    def _export(self, template_path, use_json):
        template = Template(template_path, os.getcwd(), self.s3_uploader)
        exported_template = template.export()
        return json.dumps(exported_template, indent=2, ensure_ascii=False) \
            if use_json else yaml.safe_dump(exported_template,
                                            default_flow_style=False)

    @staticmethod
    def _write_output(filename, data):
        if filename is None:
            return

        if not os.path.exists(os.path.dirname(filename)):
            try:
                os.makedirs(os.path.dirname(filename))
            except OSError as ex:
                if ex.errno != errno.EEXIST:
                    raise
        with open(filename, 'w') as fp:
            fp.write(data)
