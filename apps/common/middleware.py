import re
import os
from contextlib import contextmanager

from django.contrib.auth.models import User
from django.http import (HttpResponseForbidden, HttpResponseNotAllowed,
                         HttpResponseRedirect, HttpResponsePermanentRedirect)
from django.core.urlresolvers import is_valid_path, reverse
from django.utils.encoding import iri_to_uri

import commonware.log
from funfactory.manage import ROOT

from apps.groups.models import Group, GroupAlias

# TODO: this is hackish. Once we update mozillians to the newest playdoh layout
error_page = __import__('%s.urls' % os.path.basename(ROOT)).urls.error_page
log = commonware.log.getLogger('m.phonebook')


class PermissionDeniedMiddleware(object):
    """Add a generic 40x 'not allowed' handler.

    TODO: Currently uses the 500.html error template, but in the
    future should display a more tailored-to-the-actual-error 'not
    allowed' page.
    """

    def process_response(self, request, response):
        if isinstance(response, (HttpResponseForbidden,
                                 HttpResponseNotAllowed)):
            if request.user.is_authenticated():
                log.debug('Permission denied middleware, user was '
                          'authenticated, sending 500')
            else:
                if isinstance(response, (HttpResponseForbidden)):
                    log.debug('Response was forbidden')
                elif isinstance(response, (HttpResponseNotAllowed)):
                    log.debug('Response was not allowed')
            return error_page(request, 500, status=response.status_code)
        return response


class RemoveSlashMiddleware(object):
    """Middleware that tries to remove a trailing slash if there was a 404.

    If the response is a 404 because url resolution failed, we'll look
    for a better url without a trailing slash.

    Cribbed from kitsune:
    https://github.com/mozilla/kitsune/blob/master/apps/sumo/middleware.py

    """

    def process_response(self, request, response):
        if (response.status_code == 404
            and request.path_info.endswith('/')
            and not is_valid_path(request.path_info)
            and is_valid_path(request.path_info[:-1])):
            # Use request.path because we munged app/locale in path_info.
            newurl = request.path[:-1]
            if request.GET:
                with safe_query_string(request):
                    newurl += '?' + request.META['QUERY_STRING']
            return HttpResponsePermanentRedirect(newurl)
        return response


class UsernameRedirectionMiddleware(object):
    """
    Redirect requests for user profiles from /<username> to
    /u/<username> to avoid breaking profile urls with the new url
    schema.

    """

    def process_response(self, request, response):
        if (response.status_code == 404
            and not request.path_info.startswith('/u/')
            and not is_valid_path(request.path_info)
            and User.objects.filter(
                username__iexact=request.path_info[1:].strip('/')).exists()):

            newurl = '/u' + request.path_info
            if request.GET:
                with safe_query_string(request):
                    newurl += '?' + request.META['QUERY_STRING']
            return HttpResponseRedirect(newurl)
        return response


class OldGroupRedirectionMiddleware(object):
    """
    Redirect requests for groups from /group/<id>-<url> to
    /group/<url> to avoid breaking group urls with the new url
    schema.

    """

    def process_response(self, request, response):
        group_url = re.match('^/group/(?P<id>\d+)-(?P<url>[^/]+)$',
                             request.path_info)
        if (response.status_code == 404
            and group_url
            and (Group.objects.filter(url=group_url.group('url')).exists())):

            newurl = reverse('group', args=[group_url.group('url')])
            if request.GET:
                with safe_query_string(request):
                    newurl += '?' + request.META['QUERY_STRING']
            return HttpResponseRedirect(newurl)
        return response


class GroupAliasRedirectionMiddleware(object):
    """Redirect `group` requests to the alias `group` if it exists."""

    def process_response(self, request, response):
        group_url = re.match('^/group/(?P<url>[^/]+)$', request.path_info)
        if (response.status_code == 404
            and group_url
            and (GroupAlias.objects.filter(url=group_url.group('url'))
                 .exists())):

            group_alias = GroupAlias.objects.get(url=group_url.group('url'))
            newurl = reverse('group', args=[group_alias.alias.url])
            if request.GET:
                with safe_query_string(request):
                    newurl += '?' + request.META['QUERY_STRING']
            return HttpResponseRedirect(newurl)
        return response


@contextmanager
def safe_query_string(request):
    """Turn the QUERY_STRING into a unicode- and ascii-safe string.

    We need unicode so it can be combined with a reversed URL, but it
    has to be ascii to go in a Location header. iri_to_uri seems like
    a good compromise.
    """
    qs = request.META['QUERY_STRING']
    try:
        request.META['QUERY_STRING'] = iri_to_uri(qs)
        yield
    finally:
        request.META['QUERY_STRING'] = qs
