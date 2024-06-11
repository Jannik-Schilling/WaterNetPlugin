# -*- coding: utf-8 -*-

"""
/***************************************************************************
 WaterNets
                                 A QGIS plugin
 This plugin calculates flowpaths
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                              -------------------
        begin                : 2019-07-26
        copyright            : (C) 2019 by Jannik Schilling
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
__date__ = '2019-07-26'
__copyright__ = '(C) 2019 by Jannik Schilling'

# This will get replaced with a git SHA1 when you do a git archive

__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import *
import processing
import numpy as np

class FlowPathCalc(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_FIELD_CALC = 'INPUT_FIELD_CALC'
    INPUT_FIELD_ID = 'INPUT_FIELD_ID'
    INPUT_FIELD_NEXT = 'INPUT_FIELD_NEXT'
    INPUT_FIELD_PREV = 'INPUT_FIELD_PREV'
    OUTPUT = 'OUTPUT'


    def name(self):
        return '3 Calculate along Flow Path'

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return ''

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return FlowPathCalc()

    def shortHelpString(self):
        return self.tr(""" Workflow: 
        1. select the layer in the drop-down list \"The water network\".
        2. select the column/field to be accumulated along the flow path \"Field to calculate\"
        3. In the drop-down lists chose the columns in the attribute table created by the tool \"1 Water Network Constructor\"
        4. Click on \"Run\"
        """)


    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER,
                self.tr('The water network'),
                [QgsProcessing.TypeVectorLine]
            )
        )
        
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_FIELD_CALC,
                self.tr("Field to calculate"),
                parentLayerParameterName = self.INPUT_LAYER,
                type = QgsProcessingParameterField.Numeric,
            )
        )
        
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_FIELD_ID,
                self.tr("ID Field/NET_ID"),
                parentLayerParameterName = self.INPUT_LAYER,
                type = QgsProcessingParameterField.Any,
                defaultValue = 'NET_ID'
            )
        )
        
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_FIELD_PREV,
                self.tr("Prev Node Field/NET_FROM"),
                parentLayerParameterName = self.INPUT_LAYER,
                type = QgsProcessingParameterField.Any,
                defaultValue = 'NET_FROM'
            )
        )
        
        self.addParameter(
            QgsProcessingParameterField(
                self.INPUT_FIELD_NEXT,
                self.tr("Next Node Field/NET_TO"),
                parentLayerParameterName = self.INPUT_LAYER,
                type = QgsProcessingParameterField.Any,
                defaultValue = 'NET_TO'
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Accumulated')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(
            parameters,
            self.INPUT_LAYER,
            context
        )
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        
        '''loading the network'''
        waternet = self.parameterAsVectorLayer(parameters, self.INPUT_LAYER, context)
        wnet_fields = waternet.fields()
        '''Counter for the progress bar'''
        total = waternet.featureCount()
        parts = 100/total 

        '''names of fields for id,next segment, previous segment'''
        id_field = self.parameterAsString(parameters, self.INPUT_FIELD_ID, context)
        next_field = self.parameterAsString(parameters, self.INPUT_FIELD_NEXT, context)
        prev_field = self.parameterAsString(parameters, self.INPUT_FIELD_PREV, context)
        calc_field = self.parameterAsString(parameters, self.INPUT_FIELD_CALC, context)
        
        '''field index for id,next segment, previous segment'''
        idxId = waternet.fields().indexFromName(id_field) 
        idxPrev = waternet.fields().indexFromName(prev_field)
        idxNext = waternet.fields().indexFromName(next_field)
        idxCalc = waternet.fields().indexFromName(calc_field)


        '''load data from layer "waternet" '''
        feedback.setProgressText(self.tr("Loading network layer\n "))
        Data = [[
            str(f.attribute(idxId)),
            str(f.attribute(idxPrev)),
            str(f.attribute(idxNext)),
            f.attribute(idxCalc),
            f.id()
        ] for f in waternet.getFeatures()]
        DataArr = np.array(Data, dtype='object')
        DataArr[np.where(DataArr[:,3] == NULL),3]=0
        feedback.setProgressText(self.tr("Data loaded \n Calculating flow paths \n"))

        '''segments with numbers'''
        calc_column = np.copy(DataArr[:,3])  # deep copy of column to do calculations on
        calc_segm = np.where(calc_column > 0)[0].tolist()  # indices!
        calc_segm = [i for i in calc_segm if (DataArr[i,1] != 'unconnected' and DataArr[i,2] != 'unconnected')]
        DataArr[:,3] = 0 # set all to 0

        '''function to find next features in the net'''
        def nextFtsCalc (MARKER2):
            vtx_to = DataArr[np.where(DataArr[:,0] == MARKER2)[0].tolist(),2][0] # "to"-vertex of actual segment
            rows_to = np.where(DataArr[:,1] == vtx_to)[0].tolist() # find rows in DataArr with matching "from"-vertices to vtx_to
            unconnected_errors = [DataArr[x, 4] for x in rows_to if DataArr[x, 2]=='unconnected']  # this can only happen after manual editing
            if len(unconnected_errors) > 0:
                waternet.removeSelection()
                waternet.selectByIds(unconnected_errors, waternet.SelectBehavior(1))
                raise QgsProcessingException(
                    'The selected features in the flow are marked as \'unconnected\' '
                    + '(most likely because of manual editing). Please delete the columns with the network information ('
                    + next_field
                    + ', '
                    + prev_field
                    + ') and run tool 1 \"Water Network Constructor\" again.'
                )
            return(rows_to)

        '''function to find flow path'''
        def FlowPath (Start_Row, fp_amount):
            MARKER=DataArr[Start_Row,0] #set MARKER to ID of the first segment
            Weg = [Start_Row]    
            i=0
            while i!=len(DataArr):
                next_rows = nextFtsCalc(MARKER)
                if len(next_rows) > 1: # deviding flow path
                    calc_column[StartRow] = 0
                    calc_column[next_rows] = calc_column[next_rows]+fp_amount/len(next_rows) # this can be changed to weightet separation later
                    out = [Weg, next_rows]
                    break
                if len(next_rows) == 1: # continuing flow path
                    Weg = Weg + next_rows
                    MARKER=DataArr[next_rows[0],0] # change MARKER to Id of next segment 
                if len(next_rows) == 0: # end point
                    out = [Weg]
                    break
                i=i+1
            return (out)

        total2 = len(calc_segm)
        while len(calc_segm) > 0:
            if feedback.isCanceled():
                break
            StartRow = calc_segm[0]
            amount = calc_column[StartRow] # amount to add to flow path
            calc_column[StartRow] = 0 #"delete" calculated amount from list (set 0)
            Fl_pth = FlowPath(StartRow, amount) # get flow path of StartRow 
            if len(Fl_pth)== 2:
                calc_segm = calc_segm + Fl_pth[1] # if flow path devides add new segments to calc_segm
            DataArr[Fl_pth[0],3] = DataArr[Fl_pth[0],3]+amount # Add the amount to the calculated flow path
            calc_segm = calc_segm[1:] # delete used segment
            calc_segm = list(set(calc_segm)) #delete duplicate values
            feedback.setProgress((1-(len(calc_segm)/total2))*100)

        '''add new field'''
        new_field_name = 'calc_'+calc_field
        #define new fields
        out_fields = QgsFields()
        #append fields
        for field in wnet_fields:
            out_fields.append(QgsField(field.name(), field.type()))
        out_fields.append(QgsField(new_field_name, QVariant.Double))


        '''sink definition'''
        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            out_fields,
            source.wkbType(),
            source.sourceCrs())

        '''create output / add features to sink'''
        feedback.setProgressText(self.tr("creating output \n"))
        features = waternet.getFeatures()
#        i=0
        for (i,feature) in enumerate(features):
            # Stop the algorithm if cancel button has been clicked
            if feedback.isCanceled():
                break
            # Add a feature in the sink
            outFt = QgsFeature()
            outFt.setGeometry(feature.geometry())
            outFt.setAttributes(feature.attributes())
            outFt.setAttributes(feature.attributes()+[DataArr[i,3]])
            sink.addFeature(outFt, QgsFeatureSink.FastInsert)
            feedback.setProgress((i+1)*parts)

        return {self.OUTPUT: dest_id}

        del nextFtsCalc
        del FlowPath
        del DataArr


        return {}
