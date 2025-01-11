# -*- coding: utf-8 -*-

"""
/***************************************************************************
 WaterNets
                                 A QGIS plugin
 This plugin calculates flowpaths
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                              -------------------
        begin                : 2019-07-26
        copyright            : (C) 2020 by Jannik Schilling
        email                : jannik.schilling@uni-rostock.de
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = 'Jannik Schilling'
__date__ = '2020-01-26'
__copyright__ = '(C) 2019 by Jannik Schilling'

# This will get replaced with a git SHA1 when you do a git archive

__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessing,
    QgsProcessingException,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMultiLineString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsSpatialIndex,
    Qgis
)
import processing
import os
from collections import Counter


class WaterNetwConstructor(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    FLIP_OPTION = 'FLIP_OPTION'
    INPUT_ID_COL = 'INPUT_ID_COL'
    SEARCH_RADIUS = 'SEARCH_BUFFER'
    MULTISELECTED = 'MULTISELECTED'
    OUTPUT = 'OUTPUT'

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER,
                self.tr('Input line layer'),
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.FLIP_OPTION,
                self.tr("Flip lines according to flow direction?"),
                ['yes (from source to mouth)','no', 'against (from mouth to source)'],
                defaultValue=[0]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_ID_COL,
                self.tr("Existing ID Column"),
                parentLayerParameterName = self.INPUT_LAYER,
                type = QgsProcessingParameterField.Any,
                optional = True
            )
        )
        try:
            rad_type = Qgis.ProcessingNumberParameterType.Double
        except:
            # for qgis prior to version 3.36
            rad_type = QgsProcessingParameterNumber.Double
        param_Radius = QgsProcessingParameterNumber(
                self.SEARCH_RADIUS,
                self.tr("Search Radius for Connections"),
                type=rad_type,
                defaultValue=0,
                minValue=0,
                maxValue=10,
                optional=True
            )
        param_Radius.setFlags(param_Radius.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_Radius)

        param_multiple_netw = QgsProcessingParameterBoolean(
                self.MULTISELECTED,
                self.tr("Create multiple independent (!) networks for multiple selected outlets"),
                defaultValue=False,
                optional=True
            )
        param_multiple_netw.setFlags(param_multiple_netw.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_multiple_netw)
        
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Water network')
            )
        )
    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(
            parameters,
            self.INPUT_LAYER,
            context
        )

        flip_opt = self.parameterAsInt(parameters, self.FLIP_OPTION, context)
        search_radius = self.parameterAsDouble(parameters, self.SEARCH_RADIUS, context)
        multinetwork = self.parameterAsBool(parameters, self.MULTISELECTED, context)
        raw_layer = self.parameterAsVectorLayer(
            parameters,
            self.INPUT_LAYER,
            context
        )
        if raw_layer is None or not(raw_layer.isValid()):
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        else:
            sp_index = QgsSpatialIndex(raw_layer.getFeatures())
        raw_fields = raw_layer.fields()

        '''Counter for the progress bar'''
        total = raw_layer.featureCount()
        if total == 0:
            raise QgsProcessingException('This Layer has no features! Please check the chosen layer')
        parts = 100/total 

        '''optional: Existing ID field'''
        id_field = self.parameterAsString(parameters, self.INPUT_ID_COL, context)
        if len(id_field) == 0:
            pass
        else:
            idxid = raw_layer.fields().indexFromName(id_field)


        '''check amount of selected features'''
        sel_feats = raw_layer.selectedFeatures() #selected Feature
        if not sel_feats:
            feedback.reportError(
                self.tr(
                    '{0}: No segment selected. Please select outlet in layer "{1}" '
                ).format(self.displayName(), parameters[self.INPUT_LAYER])
            )
            raise QgsProcessingException()
        else:
            sel_feats_ids = [f.id() for f in sel_feats]
            if len(sel_feats) > 1:
                if not multinetwork:
                    feedback.reportError(
                        self.tr(
                            '{0}: Too many ({1}) segments selected. Please select only one outlet in layer "{2}" or choose the advanced option for multiple networks'
                        ).format(self.displayName(), len(sel_feats), parameters[self.INPUT_LAYER])
                    )
                    raise QgsProcessingException()
                else:
                    feedback.setProgressText('{0} segments selected. The tool will try to create multiple networks.'.format(len(sel_feats)))


        '''define (new) fields for output'''
        # define new fields
        out_fields = QgsFields()
        # append fields
        for field in raw_fields:
            out_fields.append(QgsField(field.name(), field.type()))
        out_fields.append(QgsField('NET_ID', QVariant.String))
        out_fields.append(QgsField('NET_TO', QVariant.String))
        out_fields.append(QgsField('NET_FROM', QVariant.String))
        # lists for results
        finished_segm = {}  # {qgis id: [net_id, net_to, net_from]}
        netw_dict = {}  # a dict for individual network numbers
        circ_list = []  # list for found circles
        flip_list = []  # list to flip geometries according or against flow direction


        def get_features_data(ft):
            '''
            Extracts the required data from a line feature
            :param QgsFeature ft
            :return: list with first_vertex, last_vertex, feature_id, (feature_name)
            '''
            ge = ft.geometry()
            vertex_list = [v for v in ge.vertices()]
            vert1 = QgsGeometry().fromPoint(vertex_list[0])
            vert2 = QgsGeometry().fromPoint(vertex_list[-1])
            if len(id_field) == 0:
                return [vert1, vert2, ft.id()]
            else:
                column_id = str(ft.attribute(idxid))
                return [vert1, vert2, ft.id(), column_id]

        def get_connected_ids(
            connecting_point,
            current_ft_id,
            search_radius
        ):
            '''
            Searches for connected features at the connecting point, except for the current feature; also returns the search area
            :param QgsGeometry (Point) connecting_point
            :param int current_ft_id
            :param QgsRectangle search_area
            '''
            if search_radius != 0:
                search_area = connecting_point.buffer(search_radius, 10).boundingBox()
            else:
                search_area = connecting_point.boundingBox()
            inters_list = sp_index.intersects(search_area)
            if current_ft_id in inters_list:  # remove self
                inters_list.remove(current_ft_id)
            return inters_list, search_area
            

        def prepare_visit(
            next_ft_id,
            downstream_id,
            search_area,
            flip_list,
            finished_segm,
            finished_ids
        ):
            '''
            prepares the required data for the next line segment or a segment which will be stored in the to do list
            :param int next_ft_id
            :param int downstream_id
            :param QgsRectangle search_area
            :param list flip_list
            :param dict finished_segm
            :param list finished_ids
            :return list (next_data, next_connecting_point)
            '''
            next_ft = raw_layer.getFeature(next_ft_id)
            next_data = get_features_data(next_ft)
            finished_segm[next_data[2]] = [
                        str(next_data[-1]),
                        downstream_id,
                        str(next_data[-1])
                    ]
            finished_ids.append(next_data[2])
            if next_data[0].intersects(search_area):
                next_connecting_point = next_data[1]
                flip_list.append(next_ft_id)
            else:
                next_connecting_point = next_data[0]
            return [next_data, next_connecting_point]


        '''loop for each selected'''
        for network_number, sel_feat in enumerate(sel_feats):
            finished_ids = []  # list to recognise already visited features
            to_do_list = []  # empty list for tributaries to visit later
            if multinetwork:
                sel_feats_ids = sel_feats_ids[(network_number+1):]

            '''data of first segment'''
            current_data = get_features_data(sel_feat)  # first_vertex, last_vertex, feature_id, (feature_name)
            out_marker = "Out"  # mark segment as outlet
            start_f_id = current_data[2]
            finished_segm[current_data[2]] = [
                        str(current_data[-1]),
                        out_marker,
                        str(current_data[-1])
                    ]
            finished_ids.append(current_data[2])

            '''find connecting vertex of (first) current_data, add to flip_list if conn_vert is not vert1'''
            conn_ids_0, search_area_0 = get_connected_ids(current_data[0], current_data[2], search_radius)
            conn_ids_1, search_area_1 = get_connected_ids(current_data[1], current_data[2], search_radius)
            
            if len(conn_ids_1) > 0:  # last vertex connecting
                if len(conn_ids_0) > 0:  # both vertices connecting
                    feedback.reportError(
                        self.tr(
                            'The selected segment with id == {0} is connecting two segments.'
                            +' Please chose another segment in layer "{1}" or add a segment as a single outlet'
                        ).format(current_data[2], parameters[self.INPUT_LAYER]))
                else:
                    flip_list.append(current_data[2])  # add id to flip list
                    conn_ids = conn_ids_1
                    search_area = search_area_1

            else:  # first vertex connecting
                conn_ids = conn_ids_0  
                search_area = search_area_0
            
            '''loop: while still connected features, add to finished_segm'''
            while True:
                if feedback.isCanceled():
                    print('finished so far: '+ str(finished_ids))
                    print('current id'+ str(current_data[2]))
                    break

                '''check for interconnections between networks'''
                if multinetwork:
                    check_list = [f_id for f_id in conn_ids if f_id in sel_feats_ids]
                    if check_list:
                        raise QgsProcessingException(
                            'The network which started with feature id ='
                            + str(start_f_id)
                            + ' reached other selected feature(s): '
                            +', '.join([str(f_id) for f_id in check_list])
                            + '. Please deselect one of these features or disconnect the lines'
                        )
                        break

                next_data_lists = [
                    prepare_visit(
                        next_ft_id,
                        current_data[-1],
                        search_area,
                        flip_list,
                        finished_segm,
                        finished_ids
                    ) for next_ft_id in conn_ids
                ]

                if len(conn_ids) == 0:
                    if len(to_do_list)==0:
                        netw_dict[network_number] = finished_ids
                        break
                    else:
                        current_data, connecting_point = to_do_list[0]
                        to_do_list = to_do_list[1:]
                if len(conn_ids) == 1:
                    current_data, connecting_point = next_data_lists[0]
                if len(conn_ids) > 1:
                    current_data, connecting_point = next_data_lists[0]
                    to_do_list = to_do_list + next_data_lists[1:]

                conn_ids, search_area = get_connected_ids(connecting_point, current_data[2], search_radius)

                '''check for circles'''
                circle_closing_fts = [f_id for f_id in conn_ids if f_id in finished_ids]
                if len(circle_closing_fts) > 0:
                    circ_list = circ_list + [[current_data[2], f_id] for f_id in circle_closing_fts]
                    conn_ids = [f_id for f_id in conn_ids if not f_id in finished_ids]


        '''feedback for circles'''
        if len (circ_list)>0:
            circ_dict = Counter(tuple(sorted(lst)) for lst in circ_list)
            feedback.pushWarning("Warning: Circle closed at NET_ID = ")
            for f_ids, counted in circ_dict.items():
                if counted > 1:
                    feedback.pushWarning(self.tr('{0}, ').format(str(f_ids)))


        '''sink definition'''
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            out_fields,
            raw_layer.wkbType(),
            raw_layer.sourceCrs())

        '''adjust_flip_list, if option is 2 (against)'''
        if flip_opt == 2:
            all_visited_ids = [f_id for id_list in netw_dict.values() for f_id in id_list]
            flip_list = [f_id for f_id in all_visited_ids if f_id not in flip_list]



        '''add features to sink'''
        features = raw_layer.getFeatures()
        for i, feature in enumerate(features):
            if feedback.isCanceled():
                break # Stop the algorithm if cancel button has been clicked
            old_f_id = feature.id()
            outFt = QgsFeature() # Add a feature
            if flip_opt == 0 or flip_opt == 2:
                if old_f_id in flip_list:
                    flip_geom = feature.geometry()
                    if flip_geom.isMultipart():
                        multi_geom = QgsMultiLineString()
                        for line in flip_geom.asGeometryCollection():
                            multi_geom.addGeometry(line.constGet().reversed())
                        rev_geom = QgsGeometry(multi_geom)
                    else:
                        rev_geom = QgsGeometry(flip_geom.constGet().reversed())
                    outFt.setGeometry(rev_geom)
                else:
                    outFt.setGeometry(feature.geometry())  # not in flip list
            else:
                outFt.setGeometry(feature.geometry())  # no flip option
            if old_f_id in finished_segm.keys():
                outFt.setAttributes(feature.attributes()+finished_segm[old_f_id])
            else:
                ft_data = get_features_data(feature)
                outFt.setAttributes(feature.attributes()+[str(ft_data[-1]), 'unconnected', 'unconnected'])
            sink.addFeature(outFt, QgsFeatureSink.FastInsert)


        return {self.OUTPUT: dest_id}


    def tr(self, string):
        return QCoreApplication.translate('Processing', string)


    def createInstance(self):
        return WaterNetwConstructor()

    def name(self):
        return '1 Water Network Constructor'

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""This tool creates a water network from a line layer. 
        Workflow: 
        1. chose your layer in the drop-down list 
        2. select the undermost line segment/outlet in that layer (by clicking on it) 
        3. (optional) choose a directory to save the resulting layer
        4. click on \"Run\"
        Please note: Connections will only be created at the beginning and at the end of line segments. Intersecting lines will not be connected
        The script will create three new columns in the attribute table: 
        \"NET_ID\" -- an individual identification number for every line segment, 
        \"NET_TO\" -- the NET_ID-number of the next segment (and node)
        \"NET_FROM\" -- the identification number of the previous node (which is the same as NET_ID)
        """)
