# -*- coding: utf-8 -*-
from AccessControl import getSecurityManager
from Acquisition import Explicit
from Acquisition.interfaces import IAcquirer
from Products.CMFCore.utils import getToolByName
from Products.Five.browser import BrowserView

from plone.memoize import view
from plone.app.layout.navigation.interfaces import INavtreeStrategy
from plone.app.layout.navigation.navtree import buildFolderTree
from plone.formwidget.autocomplete.widget import (
    AutocompleteMultiSelectionWidget, AutocompleteSelectionWidget
)
from plone.formwidget.contenttree.interfaces import (
    IContentTreeWidget, IContentTreeWidgetPreview, ILibraryProvider
)
from plone.formwidget.contenttree import MessageFactory as _
from plone.formwidget.contenttree.utils import closest_content
from zope.browserpage.viewpagetemplatefile import ViewPageTemplateFile
from zope.component import getMultiAdapter
from zope.component import queryMultiAdapter
from zope.i18n import translate
from zope.interface import implementer
from zope.interface import implementsOnly

import z3c.form.interfaces
import z3c.form.widget
import z3c.form.util


class BaseView(BrowserView):

    def validate_access(self):

        content = self.context.form.context

        # If the object is not wrapped in an acquisition chain
        # we cannot check any permission.
        if not IAcquirer.providedBy(content):
            return

        url = self.request.getURL()
        view_name = url[len(content.absolute_url()):].split('/')[1]

        # May raise Unauthorized

        # If the view is 'edit', then traversal prefers the view and
        # restrictedTraverse prefers the edit() method present on most CMF
        # content. Sigh...
        if not view_name.startswith('@@') and not view_name.startswith('++'):
            view_name = '@@' + view_name

        view_instance = content.restrictedTraverse(view_name)
        getSecurityManager().validate(content, content, view_name,
                                      view_instance)


class Preview(BaseView):

    def __call__(self):

        # We want to check that the user was indeed allowed to access the
        # form for this widget. We can only this now, since security isn't
        # applied yet during traversal.
        self.validate_access()

        widget = self.context
        context = widget.context

        # Update the widget before accessing the source.
        # The source was only bound without security applied
        # during traversal before.
        widget.update()
        source = widget.bound_source

        # Convert token from request to the path to the object
        token = self.request.form.get('href', None)
        directory = self.context.bound_source.tokenToPath(token)

        navtree_query = source.navigation_tree_query.copy()
        navtree_query['path'] = {'query': directory}

        if 'is_default_page' not in navtree_query:
            navtree_query['is_default_page'] = False
        if True in navtree_query['is_default_page'] and False in navtree_query['is_default_page']:
            del navtree_query['is_default_page']

        content = context
        if not IAcquirer.providedBy(content):
            content = closest_content(context)

        catalog = getToolByName(content, 'portal_catalog')
        results = catalog(navtree_query)

        if len(results) > 0:
            obj = results[0].getObject()

            preview = queryMultiAdapter(
                (obj, self.context), IContentTreeWidgetPreview)

            if preview is None:
                return _(u"No preview available.")

            return preview()


class Fetch(BaseView):

    fragment_template = ViewPageTemplateFile('fragment.pt')
    recurse_template = ViewPageTemplateFile('input_recurse.pt')

    def getTermByBrain(self, brain):
        # Ask the widget
        return self.context.getTermByBrain(brain)

    def __call__(self):
        # We want to check that the user was indeed allowed to access the
        # form for this widget. We can only this now, since security isn't
        # applied yet during traversal.
        self.validate_access()

        widget = self.context
        context = widget.context

        # Update the widget before accessing the source.
        # The source was only bound without security applied
        # during traversal before.
        widget.update()
        source = widget.bound_source

        # Convert token from request to the path to the object
        token = self.request.form.get('href', None)
        directory = self.context.bound_source.tokenToPath(token)
        level = self.request.form.get('rel', 0)

        navtree_query = source.navigation_tree_query.copy()

        if widget.show_all_content_types and 'portal_type' in navtree_query:
            del navtree_query['portal_type']

        if directory is not None:
            navtree_query['path'] = {'depth': 1, 'query': directory}

        if 'is_default_page' not in navtree_query:
            navtree_query['is_default_page'] = False
        if True in navtree_query['is_default_page'] and False in navtree_query['is_default_page']:
            del navtree_query['is_default_page']

        content = closest_content(context)

        strategy = getMultiAdapter((content, widget), INavtreeStrategy)
        catalog = getToolByName(content, 'portal_catalog')

        children = []
        for brain in catalog(navtree_query):
            newNode = {'item': brain,
                       'depth': -1,  # not needed here
                       'currentItem': False,
                       'currentParent': False,
                       'children': []}
            if strategy.nodeFilter(newNode):
                newNode = strategy.decoratorFactory(newNode)
                children.append(newNode)

        self.request.response.setHeader('X-Theme-Disabled', 'True')

        return self.fragment_template(children=children, level=int(level))


class ContentTreeBase(Explicit):
    implementsOnly(IContentTreeWidget)

    # XXX: Due to the way the rendering of the QuerySourceRadioWidget works,
    # if we call this 'template' or use a <z3c:widgetTemplate /> directive,
    # we'll get infinite recursion when trying to render the radio buttons.

    input_template = ViewPageTemplateFile('input.pt')
    hidden_template = ViewPageTemplateFile('hidden.pt')
    display_template = None  # set by subclass
    recurse_template = ViewPageTemplateFile('input_recurse.pt')

    # Parameters passed to the JavaScript function
    folderEvent = 'click'
    selectEvent = 'click'
    expandSpeed = 200
    collapseSpeed = 200
    multiFolder = True
    multi_select = False

    # Overrides for autocomplete widget
    formatItem = ('function(row, idx, count, value) {'
                  '  return row[1] + " (" + row[0] + ")"; }')

    # By default, only show 'interesting' nodes, that is: nodes that
    # are selectable or that are folders.
    show_all_nodes = False

    # By default, show all content types, even those not allowed in
    # the navigation
    show_all_content_types = True

    def getTermByBrain(self, brain):
        return self.bound_source.getTermByBrain(brain)

    @property
    @view.memoize
    def closest_content(self):
        return closest_content(self.context)

    @property
    @view.memoize
    def libraries(self):
        libraries = ILibraryProvider(self)

        # check content is already in libraries
        content = self.closest_content
        path = '/'.join(content.getPhysicalPath())
        for item in reversed(libraries):
            if item.get('query') == path:
                item['selected'] = True

                return libraries

        libraries.insert(0, {
            'label': content.title_or_id(),
            'query': path,
            'selected': True,
        })

        return libraries

    def render_tree(self):
        content = self.closest_content
        source = self.bound_source

        strategy = getMultiAdapter((content, self), INavtreeStrategy)
        data = buildFolderTree(content,
                               obj=content,
                               query=source.navigation_tree_query,
                               strategy=strategy)

        return self.recurse_template(children=data.get('children', []),
                                     level=1)

    def render(self):
        if self.mode == z3c.form.interfaces.DISPLAY_MODE:
            return self.display_template(self)
        elif self.mode == z3c.form.interfaces.HIDDEN_MODE:
            return self.hidden_template(self)
        else:
            # Dirty hack: We need to ensure that all items have the same name.
            # in z3c.form.browser.RadioWidget.update, no ":list" is appended
            # to the existing value.
            # But in z3c.formwidget.query.QuerySourceRadioWidget.update ":list"
            # is appended to the radio box's name that holds the "no value".
            # Also, this widget appends ":list" to all newly chosen values.
            # Therefore, we append ":list" to all names where it's missing.
            items = self.items
            changed = False
            for item in items:
                if item.get('name', None) and \
                        not item['name'].endswith(":list"):
                    item['name'] = item['name'] + ':list'
                    changed = True
            if changed:
                self.items = items
            return self.input_template(self)

    def js_extra(self):
        form_url = self.request.getURL()
        url = "%s/++widget++%s/@@contenttree-fetch" % (form_url, self.name)
        preview_url = "%s/++widget++%s/@@contenttree-preview" % (form_url, self.name)

        return """\
$('#%(id)s-widgets-query').each(function() {
    if($(this).siblings('input.searchButton').length > 0) { return; }
    $(document.createElement('input'))
        .attr({
            'type': 'button',
            'value': '%(button_val)s'
        })
        .addClass('searchButton')
        .click( function () {
            var parent = $(this).parents("*[id$='-autocomplete']")
            var window = parent.siblings("*[id$='-contenttree-window']")
            window.showDialog('%(url)s', %(expandSpeed)d);
            $('#' + parent.attr('id').replace('autocomplete', 'contenttree')).contentTree({
                script: '%(url)s',
                previewScript: '%(preview_url)s',
                folderEvent: '%(folderEvent)s',
                selectEvent: '%(selectEvent)s',
                expandSpeed: %(expandSpeed)d,
                collapseSpeed: %(collapseSpeed)s,
                multiFolder: %(multiFolder)s,
                multiSelect: %(multiSelect)s,
                rootUrl: '%(rootUrl)s'
            },
            function(event, selected, data, title) {
                // alert(event + ', ' + selected + ', ' + data + ', ' + title);
            });
        }).insertAfter($(this));
});
$('#%(id)s-contenttree-window').find('.contentTreeAdd').unbind('click').click(function () {
    $(this).contentTreeAdd();
});
$('#%(id)s-contenttree-window').find('.contentTreeCancel').unbind('click').click(function () {
    $(this).contentTreeCancel();
});
$('#%(id)s-widgets-query').after(" ");\
""" % dict(url=url,
           preview_url=preview_url,
           id=self.name.replace('.', '-'),
           folderEvent=self.folderEvent,
           selectEvent=self.selectEvent,
           expandSpeed=self.expandSpeed,
           collapseSpeed=self.collapseSpeed,
           multiFolder=str(self.multiFolder).lower(),
           multiSelect=str(self.multi_select).lower(),
           rootUrl=self.libraries[0]['query'],
           name=self.name,
           klass=self.klass,
           title=self.title,
           button_val=translate(
               _(u'label_contenttree_browse', default=u'browse...'),
               context=self.request))


class ContentTreeWidget(ContentTreeBase, AutocompleteSelectionWidget):
    """ContentTree widget that allows single selection.
    """

    klass = u"contenttree-widget"
    display_template = ViewPageTemplateFile('display_single.pt')


class MultiContentTreeWidget(ContentTreeBase, AutocompleteMultiSelectionWidget):
    """ContentTree widget that allows multiple selection
    """

    klass = u"contenttree-widget"
    multi_select = True
    display_template = ViewPageTemplateFile('display_multiple.pt')


@implementer(z3c.form.interfaces.IFieldWidget)
def ContentTreeFieldWidget(field, request):
    return z3c.form.widget.FieldWidget(field, ContentTreeWidget(request))


@implementer(z3c.form.interfaces.IFieldWidget)
def MultiContentTreeFieldWidget(field, request):
    return z3c.form.widget.FieldWidget(field, MultiContentTreeWidget(request))
