"""
    gui/average
    ~~~~~~~~~~~~~~~~~~~~

    Graphical user interface for three-dimensional averaging of particles

    :author: Maximilian Strauss, 2017
    :copyright: Copyright (c) 2017 Jungmann Lab, Max Planck Institute of Biochemistry
"""
import os.path
import sys
import traceback

import colorsys

import matplotlib.pyplot as plt
import numba
import numpy as np
import scipy
from scipy import signal

from PyQt4 import QtCore, QtGui

from .. import io, lib, render

from numpy.lib.recfunctions import stack_arrays

from cmath import rect, phase
from tqdm import tqdm

DEFAULT_OVERSAMPLING = 1.0
INITIAL_REL_MAXIMUM = 2.0
ZOOM = 10 / 7
N_GROUP_COLORS = 8

@numba.jit(nopython=True, nogil=True)
def render_hist(x, y, oversampling, t_min, t_max):
    n_pixel = int(np.ceil(oversampling * (t_max - t_min)))
    in_view = (x > t_min) & (y > t_min) & (x < t_max) & (y < t_max)
    x = x[in_view]
    y = y[in_view]
    x = oversampling * (x - t_min)
    y = oversampling * (y - t_min)
    image = np.zeros((n_pixel, n_pixel), dtype=np.float32)
    render._fill(image, x, y)
    return len(x), image

@numba.jit(nopython=True, nogil=True)
def render_histxyz(a, b, oversampling, a_min, a_max, b_min, b_max):
    n_pixel_a = int(np.ceil(oversampling * (a_max - a_min)))
    n_pixel_b = int(np.ceil(oversampling * (b_max - b_min)))
    in_view = (a > a_min) & (b > b_min) & (a < a_max) & (b < b_max)
    a = a[in_view]
    b = b[in_view]
    a = oversampling * (a - a_min)
    b = oversampling * (b - b_min)
    image = np.zeros((n_pixel_b, n_pixel_a), dtype=np.float32)
    render._fill(image, a, b)

    return len(a), image

def rotate_axis(axis,vx,vy,vz,angle,pixelsize):
    if axis == 'z':
        vx_rot = np.cos(angle) * vx - np.sin(angle) * vy
        vy_rot = np.sin(angle) * vx + np.cos(angle) * vy
        vz_rot = vz
    elif axis == 'y':
        vx_rot = np.cos(angle) * vx + np.sin(angle) * np.divide(vz, pixelsize)
        vy_rot = vy
        vz_rot = -np.sin(angle) * vx * pixelsize + np.cos(angle) * vz
    elif axis == 'x':
        vx_rot = vx
        vy_rot = np.cos(angle) * vy - np.sin(angle) * np.divide(vz, pixelsize)
        vz_rot = np.sin(angle) * vy * pixelsize + np.cos(angle) * vz
    return vx_rot, vy_rot, vz_rot

def compute_xcorr(CF_image_avg, image):
    F_image = np.fft.fft2(image)
    xcorr = np.fft.fftshift(np.real(np.fft.ifft2((F_image * CF_image_avg))))
    return xcorr

class ParametersDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Parameters')
        self.setModal(False)
        grid = QtGui.QGridLayout(self)

        grid.addWidget(QtGui.QLabel('Oversampling:'), 0, 0)
        self.oversampling = QtGui.QDoubleSpinBox()
        self.oversampling.setRange(1, 200)
        self.oversampling.setValue(5)
        self.oversampling.setDecimals(1)
        self.oversampling.setKeyboardTracking(False)
        self.oversampling.valueChanged.connect(self.window.updateLayout)
        grid.addWidget(self.oversampling, 0, 1)

        self.iterations = QtGui.QSpinBox()
        self.iterations.setRange(1, 1)
        self.iterations.setValue(1)

class View(QtGui.QLabel):

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setMinimumSize(1, 1)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setAcceptDrops(True)
        self._pixmap = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        path = urls[0].toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext == '.hdf5':
            self.open(path)

    def resizeEvent(self, event):
        if self._pixmap is not None:
            self.set_pixmap(self._pixmap)

    def set_image(self, image):
        cmap = np.uint8(np.round(255 * plt.get_cmap('magma')(np.arange(256))))
        image /= image.max()
        image = np.minimum(image, 1.0)
        image = np.round(255 * image).astype('uint8')
        Y, X = image.shape
        self._bgra = np.zeros((Y, X, 4), dtype=np.uint8, order='C')
        self._bgra[..., 0] = cmap[:, 2][image]
        self._bgra[..., 1] = cmap[:, 1][image]
        self._bgra[..., 2] = cmap[:, 0][image]
        qimage = QtGui.QImage(self._bgra.data, X, Y, QtGui.QImage.Format_RGB32)
        self._pixmap = QtGui.QPixmap.fromImage(qimage)
        self.set_pixmap(self._pixmap)

    def set_pixmap(self, pixmap):
        self.setPixmap(pixmap.scaled(self.width(), self.height(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation))

    def update_image(self, *args):
        oversampling = self.window.parameters_dialog.oversampling.value()
        t_min = -self.r
        t_max = self.r
        N_avg, image_avg = render.render_hist(self.locs, oversampling, t_min, t_min, t_max, t_max)
        self.set_image(image_avg)

class DatasetDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Datasets')
        self.setModal(False)
        self.layout = QtGui.QVBoxLayout()
        self.checks = []
        self.setLayout(self.layout)

    def add_entry(self,path):
        c = QtGui.QCheckBox(path)
        self.layout.addWidget(c)
        self.checks.append(c)
        self.checks[-1].setChecked(True)

class Window(QtGui.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Picasso: Average3')
        self.resize(1024, 512)
        this_directory = os.path.dirname(os.path.realpath(__file__))
        icon_path = os.path.join(this_directory, 'icons', 'average.ico')
        icon = QtGui.QIcon(icon_path)
        self.setWindowIcon(icon)
        self.setAcceptDrops(True)
        self.parameters_dialog = ParametersDialog(self)
        self.dataset_dialog = DatasetDialog(self)
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu('File')
        open_action = file_menu.addAction('Open')
        open_action.setShortcut(QtGui.QKeySequence.Open)
        open_action.triggered.connect(self.open)
        file_menu.addAction(open_action)
        save_action = file_menu.addAction('Save')
        save_action.setShortcut(QtGui.QKeySequence.Save)
        save_action.triggered.connect(self.save)
        file_menu.addAction(save_action)
        process_menu = menu_bar.addMenu('Process')
        parameters_action = process_menu.addAction('Parameters')
        parameters_action.setShortcut('Ctrl+P')
        parameters_action.triggered.connect(self.parameters_dialog.show)
        dataset_action = process_menu.addAction('Datasets')
        dataset_action.triggered.connect(self.dataset_dialog.show)

        self.status_bar = self.statusBar()
        self._pixmap = None
        self.locs = []
        self.group_index = []
        self.infos = []
        self.locs_paths = []
        self._mode = 'Zoom'
        self._pan = False
        self._size_hint = (768, 768)
        self.n_locs = 0
        self._picks = []
        self.index_blocks = []
        self._drift = []

        # Define DisplaySettingsDialog
        self.viewxy = QtGui.QLabel('')
        self.viewxz = QtGui.QLabel('')
        self.viewyz = QtGui.QLabel('')
        self.viewcp = QtGui.QLabel('')

        minsize = 512
        self.viewxy.setFixedWidth(minsize)
        self.viewxy.setFixedHeight(minsize)
        self.viewxz.setFixedWidth(minsize)
        self.viewxz.setFixedHeight(minsize)
        self.viewyz.setFixedWidth(minsize)
        self.viewyz.setFixedHeight(minsize)
        self.viewcp.setFixedWidth(minsize)
        self.viewcp.setFixedHeight(minsize)

        # Define layout
        display_groupbox = QtGui.QGroupBox('Display')
        displaygrid = QtGui.QGridLayout(display_groupbox)
        displaygrid.addWidget(QtGui.QLabel('XY'), 0, 0)
        displaygrid.addWidget(self.viewxy, 1, 0)
        displaygrid.addWidget(QtGui.QLabel('XZ'), 0, 1)
        displaygrid.addWidget(self.viewxz, 1, 1)
        displaygrid.addWidget(QtGui.QLabel('YZ'), 2, 0)
        displaygrid.addWidget(self.viewyz, 3, 0)
        displaygrid.addWidget(QtGui.QLabel('CP'), 2, 1)
        displaygrid.addWidget(self.viewcp, 3, 1)

        button_groupbox = QtGui.QGroupBox('Buttons')
        buttongrid = QtGui.QGridLayout(button_groupbox)

        rotation_groupbox = QtGui.QGroupBox('Rotation + Translation')
        rotationgrid = QtGui.QGridLayout(rotation_groupbox)
        centerofmassbtn = QtGui.QPushButton("Center of Mass XYZ")

        axis_groupbox = QtGui.QGroupBox('Axis')
        axisgrid = QtGui.QGridLayout(axis_groupbox)

        self.x_axisbtn = QtGui.QRadioButton("X")
        self.y_axisbtn = QtGui.QRadioButton("Y")
        self.z_axisbtn = QtGui.QRadioButton("Z")

        self.z_axisbtn.setChecked(True)

        axisgrid.addWidget(self.x_axisbtn, 0, 0)
        axisgrid.addWidget(self.y_axisbtn, 0, 1)
        axisgrid.addWidget(self.z_axisbtn, 0, 2)

        proj_groupbox = QtGui.QGroupBox('Projection')
        projgrid = QtGui.QGridLayout(proj_groupbox)

        self.xy_projbtn = QtGui.QRadioButton("XY")
        self.yz_projbtn = QtGui.QRadioButton("YZ")
        self.xz_projbtn = QtGui.QRadioButton("XZ")

        self.xy_projbtn.setChecked(True)

        projgrid.addWidget(self.xy_projbtn, 0, 0)
        projgrid.addWidget(self.yz_projbtn, 0, 1)
        projgrid.addWidget(self.xz_projbtn, 0, 2)

        rotatebtn = QtGui.QPushButton("Rotate")

        self.radio_sym = QtGui.QRadioButton("x symmetry")
        self.symEdit = QtGui.QSpinBox()
        self.symEdit.setRange(2, 20)
        self.symEdit.setValue(8)

        self.radio_sym_custom = QtGui.QRadioButton("custom symmetry")
        self.symcustomEdit = QtGui.QLineEdit("90,180,270")

        deg_groupbox = QtGui.QGroupBox('Degrees')
        deggrid = QtGui.QGridLayout(deg_groupbox)

        self.full_degbtn = QtGui.QRadioButton("Full")
        self.part_degbtn = QtGui.QRadioButton("Part")
        self.degEdit = QtGui.QTextEdit()

        self.degEdit = QtGui.QSpinBox()
        self.degEdit.setRange(1, 10)
        self.degEdit.setValue(5)

        deggrid.addWidget(self.full_degbtn, 0, 0)
        deggrid.addWidget(self.part_degbtn, 0, 1)
        deggrid.addWidget(self.degEdit, 0, 2)

        self.full_degbtn.setChecked(True)

        # Rotation Groupbox
        rotationgrid.addWidget(axis_groupbox, 0, 0, 1, 2)
        rotationgrid.addWidget(proj_groupbox, 1, 0, 1, 2)
        rotationgrid.addWidget(deg_groupbox, 2, 0, 1, 2)
        rotationgrid.addWidget(rotatebtn, 3, 0, 1, 2)
        rotationgrid.addWidget(self.symEdit, 4, 0)
        rotationgrid.addWidget(self.radio_sym, 4, 1)
        rotationgrid.addWidget(self.radio_sym_custom, 5, 0)
        rotationgrid.addWidget(self.symcustomEdit, 5, 1)

        buttongrid.addWidget(centerofmassbtn, 0, 0)
        buttongrid.addWidget(rotation_groupbox, 1, 0)

        centerofmassbtn.clicked.connect(self.centerofmass)
        rotatebtn.clicked.connect(self.rotate_groups)

        self.translatebtn = QtGui.QRadioButton("Translate only")

        self.alignxbtn = QtGui.QPushButton("Align X")
        self.alignybtn = QtGui.QPushButton("Align Y")
        self.alignzzbtn = QtGui.QPushButton("Align Z_Z")
        self.alignzybtn = QtGui.QPushButton("Align Z_Y")

        self.translatexbtn = QtGui.QPushButton("Translate X")
        self.translateybtn = QtGui.QPushButton("Translate Y")
        self.translatezbtn = QtGui.QPushButton("Translate Z")

        self.rotatexy_convbtn = QtGui.QPushButton('Rotate XY - Convolution')

        operate_groupbox = QtGui.QGroupBox('Operate')
        operategrid = QtGui.QGridLayout(operate_groupbox)

        rotationgrid.addWidget(self.translatebtn, 7, 0)

        operategrid.addWidget(self.alignxbtn, 0, 1)
        operategrid.addWidget(self.alignybtn, 1, 1)
        operategrid.addWidget(self.alignzzbtn, 2, 1)
        operategrid.addWidget(self.alignzybtn, 3, 1)
        operategrid.addWidget(self.translatexbtn, 0, 0)
        operategrid.addWidget(self.translateybtn, 1, 0)
        operategrid.addWidget(self.translatezbtn, 2, 0)
        operategrid.addWidget(self.rotatexy_convbtn)

        self.rotatexy_convbtn.clicked.connect(self.rotatexy_convolution)

        self.alignxbtn.clicked.connect(self.align_x)
        self.alignybtn.clicked.connect(self.align_y)
        self.alignzzbtn.clicked.connect(self.align_zz)
        self.alignzybtn.clicked.connect(self.align_zy)

        self.translatexbtn.clicked.connect(self.translate_x)
        self.translateybtn.clicked.connect(self.translate_y)
        self.translatezbtn.clicked.connect(self.translate_z)

        buttongrid.addWidget(operate_groupbox, 2, 0)

        self.contrastEdit = QtGui.QDoubleSpinBox()
        self.contrastEdit.setDecimals(1)
        self.contrastEdit.setRange(0, 10)
        self.contrastEdit.setValue(0.5)
        self.contrastEdit.setSingleStep(0.1)

        self.contrastEdit.valueChanged.connect(self.updateLayout)

        self.grid = QtGui.QGridLayout()
        self.grid.addWidget(display_groupbox, 0, 0, 2, 1)
        self.grid.addWidget(button_groupbox, 0, 1, 1, 1)

        contrast_groupbox = QtGui.QGroupBox('Contrast')
        contrastgrid = QtGui.QGridLayout(contrast_groupbox)

        contrastgrid.addWidget(self.contrastEdit)
        buttongrid.addWidget(contrast_groupbox)

        mainWidget = QtGui.QWidget()
        mainWidget.setLayout(self.grid)
        self.setCentralWidget(mainWidget)
        self.status_bar.showMessage('Average3 ready.')

    def open(self):
        path = QtGui.QFileDialog.getOpenFileName(self, 'Open localizations', filter='*.hdf5')
        if path:
            self.add(path)

    def save(self, path):
        n_channels = len(self.locs)
        for i in range(n_channels):
            cx = self.infos[i][0]['Width'] / 2
            cy = self.infos[i][0]['Height'] / 2
            out_locs = self.locs[i].copy()
            out_locs.x += cx
            out_locs.y += cy
            info = self.infos[i] + [{'Generated by': 'Picasso Average3'}]

            out_path = os.path.splitext(self.locs_paths[i])[0] + '_avg3.hdf5'
            path = QtGui.QFileDialog.getSaveFileName(self, 'Save localizations', out_path, filter='*.hdf5')
            io.save_locs(path, out_locs, info)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        path = urls[0].toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext == '.hdf5':
            print('Opening {} ..'.format(path))
            self.add(path)

    def add(self, path, rendermode=True):
        try:
            locs, info = io.load_locs(path, qt_parent=self)
        except io.NoMetadataFileError:
            return

        if len(self.locs) == 0:
            self.pixelsize = 0

        if not hasattr(locs, 'group'):
            msgBox = QtGui.QMessageBox(self)
            msgBox.setWindowTitle('Error')
            msgBox.setText('Datafile does not contain group information. Please load file with picked localizations.')
            msgBox.exec_()

        else:
            locs = lib.ensure_sanity(locs, info)
            if not hasattr(locs, 'z'):
                locs = lib.append_to_rec(locs, locs.x.copy(), 'z')
                self.pixelsize = 1
            else:
                if self.pixelsize == 0:
                    pixelsize,ok = QtGui.QInputDialog.getInt(self,"Pixelsize Dialog","Please enter the pixelsize in nm", 130)
                    if ok:
                        self.pixelsize = pixelsize
                    else:
                        self.pixelsize = 130

            self.locs.append(locs)
            self.infos.append(info)
            self.locs_paths.append(path)
            self.index_blocks.append(None)
            self._drift.append(None)
            self.dataset_dialog.add_entry(path)
            self.dataset_dialog.checks[-1].stateChanged.connect(self.updateLayout)

            if len(self.locs) == 1:
                self.median_lp = np.mean([np.median(locs.lpx), np.median(locs.lpy)])
                if hasattr(locs, 'group'):
                    groups = np.unique(locs.group)
                    groupcopy = locs.group.copy()
                    for i in range(len(groups)):
                        groupcopy[locs.group == groups[i]] = i
                    np.random.shuffle(groups)
                    groups %= N_GROUP_COLORS
                    self.group_color = groups[groupcopy]
                if render:
                    self.fit_in_view(autoscale=True)
            else:
                if render:
                    self.update_scene()

            self.oversampling = 1
            if len(self.locs) == 1:
                self.t_min = np.min([np.min(locs.x),np.min(locs.y)])
                self.t_max = np.max([np.max(locs.x),np.max(locs.y)])
                self.z_min = np.min(locs.z)
                self.z_max = np.max(locs.z)
            else:
                self.t_min = np.min([np.min(locs.x),np.min(locs.y),self.t_min])
                self.t_max = np.max([np.max(locs.x),np.max(locs.y),self.t_max])
                self.z_min = np.min([np.min(locs.z),self.z_min])
                self.z_max = np.min([np.max(locs.z),self.z_max])

            N_avg, image_avg3 = render.render_hist3d(self.locs[0], self.oversampling, self.t_min, self.t_min, self.t_max, self.t_max, self.z_min, self.z_max, self.pixelsize)

            xyproject = np.sum(image_avg3, axis=2)
            pixmap = self.histtoImage(xyproject)

            self.viewxy.setPixmap(pixmap)

            xzproject = np.transpose(np.sum(image_avg3, axis=0))
            pixmap = self.histtoImage(xzproject)
            self.viewxz.setPixmap(pixmap)

            yzproject = np.transpose(np.sum(image_avg3, axis=1))
            pixmap = self.histtoImage(yzproject)
            self.viewyz.setPixmap(pixmap)

            if len(self.locs) == 1:
                print('Dataset loaded from {}.'.format(path))
            else:
                print('Dataset loaded from {}, Total number of datasets {}.'.format(path, len(self.locs)))
                pixmap1, pixmap2, pixmap3 = self.hist_multi_channel(self.locs)

                self.viewxy.setPixmap(pixmap1)
                self.viewxz.setPixmap(pixmap2)
                self.viewyz.setPixmap(pixmap3)

            #CREATE GROUP INDEX
            if hasattr(locs, 'group'):
                groups = np.unique(locs.group)
                n_groups = len(groups)
                n_locs = len(locs)

                group_index = scipy.sparse.lil_matrix((n_groups, n_locs), dtype=np.bool)
                progress = lib.ProgressDialog('Creating group index', 0, len(groups), self)
                progress.set_value(0)
                for i, group in enumerate(groups):
                    index = np.where(locs.group == group)[0]
                    group_index[i, index] = True
                    progress.set_value(i+1)

                self.group_index.append(group_index)
                self.n_groups = n_groups
            os.chdir(os.path.dirname(path))

    def updateLayout(self):
        pixmap1, pixmap2, pixmap3 = self.hist_multi_channel(self.locs)
        self.viewxy.setPixmap(pixmap1)
        self.viewxz.setPixmap(pixmap2)
        self.viewyz.setPixmap(pixmap3)

    def centerofmass_all(self):
        #Align all by center of mass
        n_channels = len(self.locs)

        out_locs_x = []
        out_locs_y = []
        out_locs_z = []
        for j in range(n_channels):
            sel_locs_x = []
            sel_locs_y = []
            sel_locs_z = []

        #stack arrays
            sel_locs_x = self.locs[j].x
            sel_locs_y = self.locs[j].y
            sel_locs_z = self.locs[j].z
            out_locs_x.append(sel_locs_x)
            out_locs_y.append(sel_locs_y)
            out_locs_z.append(sel_locs_z)

        out_locs_x=stack_arrays(out_locs_x, asrecarray=True, usemask=False)
        out_locs_y=stack_arrays(out_locs_y, asrecarray=True, usemask=False)
        out_locs_z=stack_arrays(out_locs_z, asrecarray=True, usemask=False)

        mean_x = np.mean(out_locs_x)
        mean_y = np.mean(out_locs_y)
        mean_z = np.mean(out_locs_z)

        for j in range(n_channels):
            self.locs[j].x -= mean_x
            self.locs[j].y -= mean_y
            self.locs[j].z -= mean_z

    def centerofmass(self):
        print('Aligning by center of mass.. ', end='', flush=True)
        n_groups = self.n_groups
        n_channels = len(self.locs)
        progress = lib.ProgressDialog('Aligning by center of mass', 0, n_groups, self)
        progress.set_value(0)

        for i in range(n_groups):
            out_locs_x = []
            out_locs_y = []
            out_locs_z = []
            for j in range(n_channels):
                sel_locs_x = []
                sel_locs_y = []
                sel_locs_z = []
                index = self.group_index[j][i, :].nonzero()[1]
                # stack arrays
                sel_locs_x = self.locs[j].x[index]
                sel_locs_y = self.locs[j].y[index]
                sel_locs_z = self.locs[j].z[index]

                out_locs_x.append(sel_locs_x)
                out_locs_y.append(sel_locs_y)
                out_locs_z.append(sel_locs_z)
                progress.set_value(i+1)

            out_locs_x = stack_arrays(out_locs_x, asrecarray=True, usemask=False)
            out_locs_y = stack_arrays(out_locs_y, asrecarray=True, usemask=False)
            out_locs_z = stack_arrays(out_locs_z, asrecarray=True, usemask=False)

            mean_x = np.mean(out_locs_x)
            mean_y = np.mean(out_locs_y)
            mean_z = np.mean(out_locs_z)

            for j in range(n_channels):
                index = self.group_index[j][i, :].nonzero()[1]
                self.locs[j].x[index] -= mean_x
                self.locs[j].y[index] -= mean_y
                self.locs[j].z[index] -= mean_z

        #CALCULATE PROPER R VALUES
        self.r = 0
        self.r_z = 0
        for j in range(n_channels):
            self.r = np.max([3 * np.sqrt(np.mean(self.locs[j].x**2 + self.locs[j].y**2)),self.r])
            self.r_z = np.max([5 * np.sqrt(np.mean(self.locs[j].z**2)),self.r_z])
        self.t_min = -self.r
        self.t_max = self.r
        self.z_min = -self.r_z
        self.z_max = self.r_z

        #ENSURE SANITY
        if 0:
            self.group_index = []

            for j in range(n_channels):

                self.locs[j] = self.locs[j][self.locs[j].x > self.t_min]
                self.locs[j] = self.locs[j][self.locs[j].y > self.t_min]
                self.locs[j] = self.locs[j][self.locs[j].z > self.z_min]
                self.locs[j] = self.locs[j][self.locs[j].x < self.t_max]
                self.locs[j] = self.locs[j][self.locs[j].y < self.t_max]
                self.locs[j] = self.locs[j][self.locs[j].z < self.z_max]

                groups = np.unique(self.locs[j].group)
                n_groups = len(groups)
                n_locs = len(self.locs[j])

                group_index = scipy.sparse.lil_matrix((n_groups, n_locs), dtype=np.bool)
                progress = lib.ProgressDialog('Creating group index', 0, len(groups), self)
                progress.set_value(0)
                for i, group in enumerate(groups):
                    index = np.where(self.locs[j].group == group)[0]
                    group_index[i, index] = True
                    progress.set_value(i+1)

            self.group_index.append(group_index)
            self.n_groups = n_groups

        #go through picks, load coordinates from all sets and GOOD
        self.oversampling = 5
        self.updateLayout()
        print('Complete.')

    def histtoImage(self, image):
        cmap = np.uint8(np.round(255 * plt.get_cmap('magma')(np.arange(256))))
        image /= image.max()
        image = np.minimum(image, 1.0)
        image = np.round(255 * image).astype('uint8')
        Y, X = image.shape
        self._bgra = np.zeros((Y, X, 4), dtype=np.uint8, order='C')
        self._bgra[..., 0] = cmap[:, 2][image]
        self._bgra[..., 1] = cmap[:, 1][image]
        self._bgra[..., 2] = cmap[:, 0][image]
        qimage = QtGui.QImage(self._bgra.data, X, Y, QtGui.QImage.Format_RGB32)

        qimage = qimage.scaled(self.viewxy.width(), np.round(self.viewxy.height()*Y/X), QtCore.Qt.KeepAspectRatioByExpanding)
        pixmap = QtGui.QPixmap.fromImage(qimage)

        return pixmap

    def hist_multi_channel(self, locs):

        oversampling = self.parameters_dialog.oversampling.value()
        self.oversampling = oversampling
        if locs is None:
            locs = self.locs
        n_channels = len(locs)

        hues = np.arange(0, 1, 1 / n_channels)
        colors = [colorsys.hsv_to_rgb(_, 1, 1) for _ in hues]

        renderings = []
        for i in range(n_channels):
            if self.dataset_dialog.checks[i].isChecked():
                renderings.append(render.render_hist3d(locs[i], oversampling, self.t_min, self.t_min, self.t_max, self.t_max, self.z_min, self.z_max, self.pixelsize))
        n_locs = sum([_[0] for _ in renderings])
        images = np.array([_[1] for _ in renderings])

        pixmap1 = self.pixmap_from_colors(images,colors,2)
        pixmap2 = self.pixmap_from_colors(images,colors,0)
        pixmap3 = self.pixmap_from_colors(images,colors,1)

        return pixmap1, pixmap2, pixmap3

    def pixmap_from_colors(self,images,colors,axisval):
        if axisval == 2:
            image = [np.sum(_, axis=axisval) for _ in images]
        else:
            image = [np.transpose(np.sum(_, axis=axisval)) for _ in images]
        image = np.array([self.scale_contrast(_) for _ in image])
        Y, X = image.shape[1:]
        bgra = np.zeros((Y, X, 4), dtype=np.float32)
        for color, image in zip(colors, image):
            bgra[:, :, 0] += color[2] * image
            bgra[:, :, 1] += color[1] * image
            bgra[:, :, 2] += color[0] * image
        bgra = np.minimum(bgra, 1)
        self._bgra = self.to_8bit(bgra)

        qimage = QtGui.QImage(self._bgra.data, X, Y, QtGui.QImage.Format_RGB32)

        qimage = qimage.scaled(self.viewxy.width(), np.round(self.viewxy.height()*Y/X), QtCore.Qt.KeepAspectRatioByExpanding)
        pixmap = QtGui.QPixmap.fromImage(qimage)

        return pixmap

    def align_x(self):
        print('Align X')
        self.align_all('x')

    def align_y(self):
        print('Align Y')
        self.align_all('y')

    def align_zz(self):
        print('Align Z')
        self.align_all('zz')

    def align_zy(self):
        print('Align Z')
        self.align_all('zy')

    def translate_x(self):
        print('Translate X')
        self.translate('x')

    def translate_y(self):
        print('Translate Y')
        self.translate('y')

    def translate_z(self):
        print('Translate Z')
        self.translate('z')

    def translate(self, translateaxis):
        renderings = [render.render_hist3d(_, self.oversampling, self.t_min, self.t_min, self.t_max, self.t_max, self.z_min, self.z_max, self.pixelsize) for _ in self.locs]
        n_locs = sum([_[0] for _ in renderings])
        images = np.array([_[1] for _ in renderings])

        if translateaxis == 'x':
            image = [np.sum(_, axis=2) for _ in images]
            signalimg = [np.sum(_, axis=0) for _ in image]
        elif translateaxis == 'y':
            image = [np.sum(_, axis=2) for _ in images]
            signalimg = [np.sum(_, axis=1) for _ in image]
        elif translateaxis == 'z':
            image = [np.sum(_, axis=1) for _ in images]
            signalimg = [np.sum(_, axis=0) for _ in image]

        fig = plt.figure(figsize =(5,5))
        ax1 = fig.add_subplot(1, 1 ,1)
        for element in signalimg:
            plt.plot(element)
        n_groups = self.group_index[0].shape[0]
        print('Translating..')
        for i in tqdm(range(n_groups)):
            self.status_bar.showMessage('Group {} / {}.'.format(i, n_groups))
            self.translate_group(signalimg, i, translateaxis)

        fig.canvas.draw()
        size = fig.canvas.size()
        width, height = size.width(), size.height()
        im = QtGui.QImage(fig.canvas.buffer_rgba(), width, height, QtGui.QImage.Format_ARGB32)
        self.viewcp.setPixmap((QtGui.QPixmap(im)))
        self.viewcp.setAlignment(QtCore.Qt.AlignCenter)
        plt.close(fig)

        self.centerofmass_all()
        self.updateLayout()
        self.status_bar.showMessage('Done!')

    def translate_group(self, signalimg, group, translateaxis):
        n_channels = len(self.locs)
        all_xcorr = np.zeros((1, n_channels))
        all_da = np.zeros((1, n_channels))

        if translateaxis == 'x':
            proplane = 'xy'
        elif translateaxis == 'y':
            proplane = 'xy'
        elif translateaxis == 'z':
            proplane = 'xz'

        plotmode = 0

        for j in range(n_channels):
            if plotmode:
                fig = plt.figure()
                ax1 = fig.add_subplot(1, 3, 1)
                plt.plot(signalimg[j])
                ax2 = fig.add_subplot(1, 3, 2)

            if self.dataset_dialog.checks[j].isChecked():
                index = self.group_index[j][group].nonzero()[1]
                x_rot = self.locs[j].x[index]
                y_rot = self.locs[j].y[index]
                z_rot = self.locs[j].z[index]

                xcorr_max = 0.0
                plane = self.render_planes(x_rot, y_rot, z_rot, proplane, self.pixelsize) #
                if translateaxis == 'x':
                    projection = np.sum(plane, axis=0)
                elif translateaxis == 'y':
                    projection = np.sum(plane, axis=1)
                elif translateaxis == 'z':
                    projection = np.sum(plane, axis=1)

                if plotmode:
                    plt.plot(projection)
                #print('Step X')
                #ax3 = fig.add_subplot(1,3,3)
                #plt.imshow(plane, interpolation='nearest', cmap=plt.cm.ocean)
                corrval = np.max(signal.correlate(signalimg[j],projection))
                shiftval = np.argmax(signal.correlate(signalimg[j], projection))-len(signalimg[j])+1
                all_xcorr[0,j] = corrval
                all_da[0,j] = shiftval/self.oversampling

            if plotmode:
                plt.show()

        #value with biggest cc value form table
        maximumcc = np.argmax(np.sum(all_xcorr,axis = 1))
        dafinal = np.mean(all_da[maximumcc,:])
        for j in range(n_channels):
            index = self.group_index[j][group].nonzero()[1]
            if translateaxis == 'x':
                self.locs[j].x[index] += dafinal
            elif translateaxis == 'y':
                self.locs[j].y[index] += dafinal
            elif translateaxis == 'z':
                self.locs[j].z[index] += dafinal*self.pixelsize

    def rotatexy_convolution_group(self, CF_image_avg, angles, group, rotaxis, proplane):
        n_channels = len(self.locs)
        allrot = []
        alldx = []
        alldy = []
        alldz = []

        n_angles = len(angles)

        all_xcorr = np.zeros((n_angles,n_channels))
        all_da = np.zeros((n_angles,n_channels))
        all_db = np.zeros((n_angles,n_channels))

        for j in range(n_channels):
            if self.dataset_dialog.checks[j].isChecked():
                index = self.group_index[j][group].nonzero()[1]
                x_rot = self.locs[j].x[index]
                y_rot = self.locs[j].y[index]
                z_rot = self.locs[j].z[index]
                x_original = x_rot.copy()
                y_original = y_rot.copy()
                z_original = z_rot.copy()
                xcorr_max = 0.0

                if self.translatebtn.isChecked():
                    angles = [0]
                    n_angles = 1

                for k in range(n_angles):
                    angle = angles[k]
                    # rotate locs
                    x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, angle, self.pixelsize)
                    # render group image for plane
                    image = self.render_planes(x_rot, y_rot, z_rot, proplane, self.pixelsize)

                    # calculate cross-correlation
                    if 0:
                        fig = plt.figure()
                        ax1 = fig.add_subplot(1,2,1)
                        ax1.set_aspect('equal')
                        plt.imshow(image, interpolation='nearest', cmap=plt.cm.ocean)
                        plt.colorbar()
                        plt.show()
                        plt.waitforbuttonpress()

                    xcorr = np.sum(np.multiply(CF_image_avg[j], image))
                    all_xcorr[k,j] = xcorr

        #value with biggest cc value form table
        maximumcc = np.argmax(np.sum(all_xcorr,axis = 1))
        rotfinal = angles[maximumcc]

        dafinal = np.mean(all_da[maximumcc,:])
        dbfinal = np.mean(all_db[maximumcc,:])

        for j in range(n_channels):
            index = self.group_index[j][group].nonzero()[1]
            x_rot = self.locs[j].x[index]
            y_rot = self.locs[j].y[index]
            z_rot = self.locs[j].z[index]
            x_original = x_rot.copy()
            y_original = y_rot.copy()
            z_original = z_rot.copy()
            # rotate and shift image group locs
            x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, rotfinal, self.pixelsize)

            self.locs[j].x[index] = x_rot
            self.locs[j].y[index] = y_rot
            self.locs[j].z[index] = z_rot

    def rotatexy_convolution(self):
        #TODO: re-write ths with kwargs at some point
        rotaxis = []
        if self.x_axisbtn.isChecked():
            rotaxis = 'x'
        elif self.y_axisbtn.isChecked():
            rotaxis = 'y'
        elif self.z_axisbtn.isChecked():
            rotaxis = 'z'

        n_groups = self.group_index[0].shape[0]
        a_step = np.arcsin(1 / (self.oversampling * self.r))
        if self.full_degbtn.isChecked():
            angles = np.arange(0, 2*np.pi, a_step)
        elif self.part_degbtn.isChecked():
            degree = self.degEdit.value()
            angles = np.arange(-degree/360*2*np.pi, degree/360*2*np.pi, a_step)

        renderings = [render.render_hist3d(_, self.oversampling, self.t_min, self.t_min, self.t_max, self.t_max, self.z_min, self.z_max, self.pixelsize) for _ in self.locs]
        n_locs = sum([_[0] for _ in renderings])
        images = np.array([_[1] for _ in renderings])

        #DELIVER CORRECT PROJECTION FOR IMAGE
        proplane = []

        if self.xy_projbtn.isChecked():
            proplane = 'xy'
            image = [np.sum(_, axis=2) for _ in images]
        elif self.yz_projbtn.isChecked():
            proplane = 'yz'
            image = [np.sum(_, axis=1) for _ in images]
            image = [_.transpose() for _ in image]
        elif self.xz_projbtn.isChecked():
            proplane = 'xz'
            image = [(np.sum(_, axis=0)) for _ in images]
            image = [_.transpose() for _ in image]

        #Change CFiamge for symmetry
        if self.radio_sym.isChecked():
            print('Using symmetry.')
            fig = plt.figure(figsize =(5,5))
            ax1 = fig.add_subplot(1,2,1)
            symmetry = self.symEdit.value()
            ax1.set_aspect('equal')
            imageold = image[0].copy()
            plt.imshow(imageold, interpolation='nearest', cmap=plt.cm.ocean)

            #rotate image
            for i in range(symmetry-1):
                image[0] += scipy.ndimage.interpolation.rotate(imageold,((i+1)*360/symmetry) , axes=(1, 0),reshape=False)

            ax2 = fig.add_subplot(1,2,2)
            ax2.set_aspect('equal')
            plt.imshow(image[0], interpolation='nearest', cmap=plt.cm.ocean)
            fig.canvas.draw()
            size = fig.canvas.size()
            width, height = size.width(), size.height()
            im = QtGui.QImage(fig.canvas.buffer_rgba(), width, height, QtGui.QImage.Format_ARGB32)
            self.viewcp.setPixmap((QtGui.QPixmap(im)))
            self.viewcp.setAlignment(QtCore.Qt.AlignCenter)
            plt.close(fig)


        if self.radio_sym_custom.isChecked():
            print('Using custom symmetry.')
            symmetry_txt = np.asarray((self.symcustomEdit.text()).split(','))
            print(symmetry_txt)
            fig = plt.figure(figsize =(5,5))
            ax1 = fig.add_subplot(1,2,1)
            symmetry = self.symEdit.value()
            ax1.set_aspect('equal')
            imageold = image[0].copy()
            plt.imshow(imageold, interpolation='nearest', cmap=plt.cm.ocean)

            #rotate image
            for degree in symmetry_txt:
                image[0] += scipy.ndimage.interpolation.rotate(imageold, float(degree) , axes=(1, 0),reshape=False)

            ax2 = fig.add_subplot(1,2,2)
            ax2.set_aspect('equal')
            plt.imshow(image[0], interpolation='nearest', cmap=plt.cm.ocean)
            fig.canvas.draw()
            size = fig.canvas.size()
            width, height = size.width(), size.height()
            im = QtGui.QImage(fig.canvas.buffer_rgba(), width, height, QtGui.QImage.Format_ARGB32)
            self.viewcp.setPixmap((QtGui.QPixmap(im)))
            self.viewcp.setAlignment(QtCore.Qt.AlignCenter)
            plt.close(fig)

        CF_image_avg = image

        # TODO: blur auf average !!!
        print('Convolving..')
        for i in tqdm(range(n_groups)):
            self.status_bar.showMessage('Group {} / {}.'.format(i,n_groups))
            self.rotatexy_convolution_group(CF_image_avg, angles, i, rotaxis, proplane)
        self.updateLayout()
        self.status_bar.showMessage('Done!')

    def rotate_groups(self):
        #Read out values from radiobuttons
        #TODO: maybe re-write this with kwargs
        rotaxis = []
        if self.x_axisbtn.isChecked():
            rotaxis = 'x'
        elif self.y_axisbtn.isChecked():
            rotaxis = 'y'
        elif self.z_axisbtn.isChecked():
            rotaxis = 'z'

        n_groups = self.group_index[0].shape[0]
        a_step = np.arcsin(1 / (self.oversampling * self.r))

        if self.full_degbtn.isChecked():
            angles = np.arange(0, 2*np.pi, a_step)
        elif self.part_degbtn.isChecked():
            degree = self.degEdit.value()
            angles = np.arange(-degree/360*2*np.pi, degree/360*2*np.pi, a_step)


        renderings = [render.render_hist3d(_, self.oversampling, self.t_min, self.t_min, self.t_max, self.t_max, self.z_min, self.z_max, self.pixelsize) for _ in self.locs]
        n_locs = sum([_[0] for _ in renderings])
        images = np.array([_[1] for _ in renderings])

        #DELIVER CORRECT PROJECTION FOR IMAGE
        proplane = []
        if self.xy_projbtn.isChecked():

            proplane = 'xy'
            image = [np.sum(_, axis=2) for _ in images]
        elif self.yz_projbtn.isChecked():

            proplane = 'yz'

            image = [np.sum(_, axis=1) for _ in images]
            image = [_.transpose() for _ in image]
        elif self.xz_projbtn.isChecked():

            proplane = 'xz'
            image = [(np.sum(_, axis=0)) for _ in images]
            image = [_.transpose() for _ in image]

        if self.radio_sym.isChecked():
            print('Radio sym')
            fig = plt.figure(figsize = (5,5))
            ax1 = fig.add_subplot(1,2,1)
            symmetry = self.symEdit.value()
            ax1.set_aspect('equal')
            imageold = image[0].copy()
            plt.imshow(imageold, interpolation='nearest', cmap=plt.cm.ocean)

            #rotate image
            for i in range(symmetry-1):
                image[0] += scipy.ndimage.interpolation.rotate(imageold,((i+1)*360/symmetry) , axes=(1, 0),reshape=False)

            ax2 = fig.add_subplot(1,2,2)
            ax2.set_aspect('equal')
            plt.imshow(image[0], interpolation='nearest', cmap=plt.cm.ocean)
            fig.canvas.draw()
            size = fig.canvas.size()
            width, height = size.width(), size.height()
            im = QtGui.QImage(fig.canvas.buffer_rgba(), width, height, QtGui.QImage.Format_ARGB32)
            self.viewcp.setPixmap((QtGui.QPixmap(im)))
            self.viewcp.setAlignment(QtCore.Qt.AlignCenter)
            plt.close(fig)

        #TODO: Sort these functions out, combine with radio_sym / also for convolving.
        if self.radio_sym_custom.isChecked():
            print('Using custom symmetry.')
            symmetry_txt = np.asarray((self.symcustomEdit.text()).split(','))
            fig = plt.figure(figsize =(5,5))
            ax1 = fig.add_subplot(1,2,1)
            symmetry = self.symEdit.value()
            ax1.set_aspect('equal')
            imageold = image[0].copy()
            plt.imshow(imageold, interpolation='nearest', cmap=plt.cm.ocean)

            #rotate image
            for degree in symmetry_txt:
                image[0] += scipy.ndimage.interpolation.rotate(imageold, float(degree) , axes=(1, 0),reshape=False)

            ax2 = fig.add_subplot(1,2,2)
            ax2.set_aspect('equal')
            plt.imshow(image[0], interpolation='nearest', cmap=plt.cm.ocean)
            fig.canvas.draw()
            size = fig.canvas.size()
            width, height = size.width(), size.height()
            im = QtGui.QImage(fig.canvas.buffer_rgba(), width, height, QtGui.QImage.Format_ARGB32)
            self.viewcp.setPixmap((QtGui.QPixmap(im)))
            self.viewcp.setAlignment(QtCore.Qt.AlignCenter)
            plt.close(fig)


        CF_image_avg = image

        CF_image_avg = [np.conj(np.fft.fft2(_)) for _ in image]
        #n_pixel, _ = image_avg.shape
        #image_half = n_pixel / 2

        # TODO: blur auf average !!!
        print('Rotating..')
        for i in tqdm(range(n_groups)):
            self.status_bar.showMessage('Group {} / {}.'.format(i,n_groups))
            self.align_group(CF_image_avg, angles, i, rotaxis, proplane)
        self.updateLayout()
        self.status_bar.showMessage('Done!')

    def mean_angle(self, deg):
        return (phase(sum(rect(1, d) for d in deg)/len(deg)))


    def render_planes(self, xdata, ydata, zdata, proplane, pixelsize):
        #assign correct renderings for all planes
        a_render = []
        b_render = []

        if proplane == 'xy':
            a_render = xdata
            b_render = ydata
            aval_min = self.t_min
            aval_max = self.t_max
            bval_min = self.t_min
            bval_max = self.t_max
        elif proplane == 'yz':
            a_render = ydata
            b_render = np.divide(zdata,pixelsize)
            aval_min = self.t_min
            aval_max = self.t_max
            bval_min = np.divide(self.z_min,pixelsize)
            bval_max = np.divide(self.z_max,pixelsize)
        elif proplane == 'xz':
            b_render = np.divide(zdata, pixelsize)
            a_render = xdata
            bval_min = np.divide(self.z_min,pixelsize)
            bval_max = np.divide(self.z_max,pixelsize)
            aval_min = self.t_min
            aval_max = self.t_max

        N, plane = render_histxyz(a_render, b_render, self.oversampling, aval_min, aval_max, bval_min, bval_max)
        return plane

    def align_all(self, alignaxis):
        a_step = np.arcsin(1 / (self.oversampling * self.r))
        angles = np.arange(0, 2*np.pi, a_step)
        n_channels = len(self.locs)
        allrot = []
        n_angles = len(angles)
        all_corr = np.zeros((n_angles,n_channels))

        for j in range(n_channels):
            if self.dataset_dialog.checks[j].isChecked():
                alignimage = []
                x_rot = self.locs[j].x
                y_rot = self.locs[j].y
                z_rot = self.locs[j].z

                x_original = x_rot.copy()
                y_original = y_rot.copy()
                z_original = z_rot.copy()

                alignimage = []

                for k in range(n_angles):
                    angle = angles[k]
                    if alignaxis == 'zz':
                        proplane = 'yz'
                        rotaxis = 'x'
                    elif alignaxis == 'zy':
                        proplane = 'yz'
                        rotaxis = 'x'
                    elif alignaxis == 'y':
                        proplane = 'xy'
                        rotaxis = 'z'
                    elif alignaxis == 'x':
                        proplane = 'xy'
                        rotaxis = 'z'

                    x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, angle,self.pixelsize)
                    # render group image for plane
                    image = self.render_planes(x_rot, y_rot, z_rot, proplane, self.pixelsize) #RENDR PLANES WAS BUGGY AT SOME POINT

                    if alignimage == []:
                        alignimage = np.zeros(image.shape)
                    #CREATE ALIGNIMAGE
                        if alignaxis == 'zz':
                            alignimage[np.int(alignimage.shape[0]/2),:]+=2
                            alignimage[np.int(alignimage.shape[0]/2)+1,:]+=1
                            alignimage[np.int(alignimage.shape[0]/2)-1,:]+=1
                        elif alignaxis == 'zy':
                            alignimage[:,np.int(alignimage.shape[0]/2)]+=2
                            alignimage[:,np.int(alignimage.shape[0]/2)+1]+=1
                            alignimage[:,np.int(alignimage.shape[0]/2)-1]+=1
                        elif alignaxis == 'y':
                            alignimage[:,np.int(alignimage.shape[1]/2)]+=2
                            alignimage[:,np.int(alignimage.shape[1]/2)-1]+=1
                            alignimage[:,np.int(alignimage.shape[1]/2)+1]+=1
                        elif alignaxis == 'x':
                            alignimage[np.int(alignimage.shape[0]/2),:]+=2
                            alignimage[np.int(alignimage.shape[0]/2)+1,:]+=1
                            alignimage[np.int(alignimage.shape[0]/2)-1,:]+=1

                    all_corr[k,j] = np.sum(np.multiply(alignimage,image))

                    if 0:
                        fig = plt.figure()
                        ax1 = fig.add_subplot(1,2,1)
                        ax1.set_aspect('equal')
                        plt.imshow(image, interpolation='nearest', cmap=plt.cm.ocean)
                        ax2 = fig.add_subplot(1,2,2)
                        ax2.set_aspect('equal')
                        plt.imshow(alignimage, interpolation='nearest', cmap=plt.cm.ocean)
                        plt.colorbar()
                        plt.show()

        #value with biggest cc value form table
        maximumcc = np.argmax(np.sum(all_corr,axis = 1))
        rotfinal = angles[maximumcc]

        for j in range(n_channels):
            x_rot = self.locs[j].x
            y_rot = self.locs[j].y
            z_rot = self.locs[j].z
            x_original = x_rot.copy()
            y_original = y_rot.copy()
            z_original = z_rot.copy()
            # rotate and shift image group locs

            x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, rotfinal,self.pixelsize)
            self.locs[j].x = x_rot
            self.locs[j].y = y_rot
            self.locs[j].z = z_rot

        self.updateLayout()
        self.status_bar.showMessage('Align on Axis {} complete.'.format(alignaxis))

    def align_group(self, CF_image_avg, angles, group, rotaxis, proplane):
        n_channels = len(self.locs)
        allrot = []
        alldx = []
        alldy = []
        alldz = []

        n_angles = len(angles)

        all_xcorr = np.zeros((n_angles,n_channels))
        all_da = np.zeros((n_angles,n_channels))
        all_db = np.zeros((n_angles,n_channels))

        for j in range(n_channels):
            if self.dataset_dialog.checks[j].isChecked():
                index = self.group_index[j][group].nonzero()[1]
                x_rot = self.locs[j].x[index]
                y_rot = self.locs[j].y[index]
                z_rot = self.locs[j].z[index]
                x_original = x_rot.copy()
                y_original = y_rot.copy()
                z_original = z_rot.copy()
                xcorr_max = 0.0

                if self.translatebtn.isChecked():
                    angles = [0]
                    n_angles = 1

                for k in range(n_angles):
                    angle = angles[k]

                    x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, angle, self.pixelsize)
                    # render group image for plane
                    image = self.render_planes(x_rot, y_rot, z_rot, proplane, self.pixelsize) #RENDR PLANES WAS BUGGY AT SOME POINT

                    # calculate cross-correlation
                    if 0:
                        fig = plt.figure()
                        ax1 = fig.add_subplot(1,2,1)
                        ax1.set_aspect('equal')
                        plt.imshow(image, interpolation='nearest', cmap=plt.cm.ocean)
                        plt.colorbar()
                        plt.show()
                        plt.waitforbuttonpress()

                    xcorr = compute_xcorr(CF_image_avg[j], image)

                    n_pixelb, n_pixela = image.shape
                    image_halfa = n_pixela / 2 #TODO: CHECK THOSE VALUES
                    image_halfb = n_pixelb / 2

                    # find the brightest pixel
                    b_max, a_max = np.unravel_index(xcorr.argmax(), xcorr.shape)
                    # store the transformation if the correlation is larger than before
                    all_xcorr[k,j] = xcorr[b_max, a_max]
                    all_db[k,j] = np.ceil(b_max - image_halfb) / self.oversampling
                    all_da[k,j] = np.ceil(a_max - image_halfa) / self.oversampling

        #value with biggest cc value form table
        maximumcc = np.argmax(np.sum(all_xcorr,axis = 1))
        rotfinal = angles[maximumcc]

        dafinal = np.mean(all_da[maximumcc,:])
        dbfinal = np.mean(all_db[maximumcc,:])

        for j in range(n_channels):
            index = self.group_index[j][group].nonzero()[1]
            x_rot = self.locs[j].x[index]
            y_rot = self.locs[j].y[index]
            z_rot = self.locs[j].z[index]
            x_original = x_rot.copy()
            y_original = y_rot.copy()
            z_original = z_rot.copy()
            # rotate and shift image group locs
            x_rot, y_rot, z_rot = rotate_axis(rotaxis, x_original, y_original, z_original, rotfinal, self.pixelsize)

            self.locs[j].x[index] = x_rot
            self.locs[j].y[index] = y_rot
            self.locs[j].z[index] = z_rot

            #Shift image group locs
            if self.translatebtn.isChecked():
                dbfinal = 0
            if proplane == 'xy':
                self.locs[j].x[index] -= dafinal
                self.locs[j].y[index] -= dbfinal
            elif proplane == 'yz':
                self.locs[j].y[index] -= dafinal
                self.locs[j].z[index] -= dbfinal*self.pixelsize
            elif proplane == 'xz':
                self.locs[j].z[index] -= dafinal
                self.locs[j].x[index] -= dbfinal*self.pixelsize

    def fit_in_view(self, autoscale=False):
        movie_height, movie_width = self.movie_size()
        viewport = [(0, 0), (movie_height, movie_width)]
        self.update_scene(viewport=viewport, autoscale=autoscale)

    def movie_size(self):
        movie_height = self.max_movie_height()
        movie_width = self.max_movie_width()
        return (movie_height, movie_width)

    def max_movie_height(self):
        ''' Returns maximum height of all loaded images. '''
        return max(info[0]['Height'] for info in self.infos)

    def max_movie_width(self):
        return max([info[0]['Width'] for info in self.infos])

    def update_scene(self, viewport=None, autoscale=False, use_cache=False, picks_only=False):
        n_channels = len(self.locs)
        if n_channels:
            viewport = viewport or self.viewport
            self.draw_scene(viewport, autoscale=autoscale, use_cache=use_cache, picks_only=picks_only)
            #self.update_cursor()

    def draw_scene(self, viewport, autoscale=False, use_cache=False, picks_only=False):
        self.viewport = self.adjust_viewport_to_view(viewport)
        qimage = self.render_scene(autoscale=autoscale, use_cache=use_cache)
        self.qimage = qimage.scaled(self.viewxy.width(), self.viewxy.height(), QtCore.Qt.KeepAspectRatioByExpanding)
        pixmap = QtGui.QPixmap.fromImage(self.qimage)
        self.viewxy.setPixmap(pixmap)
        self.viewxz.setPixmap(pixmap)
        self.viewyz.setPixmap(pixmap)
        self.viewcp.setPixmap(pixmap)

    def adjust_viewport_to_view(self, viewport):
        ''' Adds space to a desired viewport so that it matches the window aspect ratio. '''
        viewport_height = viewport[1][0] - viewport[0][0]
        viewport_width = viewport[1][1] - viewport[0][1]
        view_height = self.height()
        view_width = self.width()
        viewport_aspect = viewport_width / viewport_height
        view_aspect = view_width / view_height
        if view_aspect >= viewport_aspect:
            y_min = viewport[0][0]
            y_max = viewport[1][0]
            x_range = viewport_height * view_aspect
            x_margin = (x_range - viewport_width) / 2
            x_min = viewport[0][1] - x_margin
            x_max = viewport[1][1] + x_margin
        else:
            x_min = viewport[0][1]
            x_max = viewport[1][1]
            y_range = viewport_width / view_aspect
            y_margin = (y_range - viewport_height) / 2
            y_min = viewport[0][0] - y_margin
            y_max = viewport[1][0] + y_margin
        return [(y_min, x_min), (y_max, x_max)]

    def render_scene(self, autoscale=False, use_cache=False, cache=True, viewport=None):
        kwargs = self.get_render_kwargs(viewport=viewport)
        n_channels = len(self.locs)
        if n_channels == 1:
            self.render_single_channel(kwargs, autoscale=autoscale, use_cache=use_cache, cache=cache)
        else:
            self.render_multi_channel(kwargs, autoscale=autoscale, use_cache=use_cache, cache=cache)
        self._bgra[:, :, 3].fill(255)
        Y, X = self._bgra.shape[:2]
        qimage = QtGui.QImage(self._bgra.data, X, Y, QtGui.QImage.Format_RGB32)
        return qimage

    def get_render_kwargs(self, viewport=None):  #Dummy for now: TODO: Implement
        viewport = [(0, 0), (32, 32)]
        return {'oversampling': 5,
            'viewport': viewport,
            'blur_method': None,
            'min_blur_width': float(0)}

    def render_multi_channel(self, kwargs, autoscale=False, locs=None, use_cache=False, cache=True):
        if locs is None:
            locs = self.locs
        n_channels = len(locs)
        hues = np.arange(0, 1, 1 / n_channels)
        colors = [colorsys.hsv_to_rgb(_, 1, 1) for _ in hues]
        if use_cache:
            n_locs = self.n_locs
            image = self.image
        else:
            renderings = [render.render(_, **kwargs) for _ in locs]
            n_locs = sum([_[0] for _ in renderings])
            image = np.array([_[1] for _ in renderings])
        if cache:
            self.n_locs = n_locs
            self.image = image
        image = self.scale_contrast(image)
        Y, X = image.shape[1:]
        bgra = np.zeros((Y, X, 4), dtype=np.float32)
        for color, image in zip(colors, image):
            bgra[:, :, 0] += color[2] * image
            bgra[:, :, 1] += color[1] * image
            bgra[:, :, 2] += color[0] * image
        bgra = np.minimum(bgra, 1)
        self._bgra = self.to_8bit(bgra)
        return self._bgra

    def render_single_channel(self, kwargs, autoscale=False, use_cache=False, cache=True):
        locs = self.locs[0]
        if hasattr(locs, 'group'):
            locs = [locs[self.group_color == _] for _ in range(N_GROUP_COLORS)]
            return self.render_multi_channel(kwargs, autoscale=autoscale, locs=locs, use_cache=use_cache)
        if use_cache:
            n_locs = self.n_locs
            image = self.image
        else:
            n_locs, image = render.render(locs, **kwargs)
        if cache:
            self.n_locs = n_locs
            self.image = image
        image = self.scale_contrast(image, autoscale=autoscale)
        image = self.to_8bit(image)
        Y, X = image.shape
        #cmap = self.window.display_settings_dialog.colormap.currentText() TODO: selection of colormap?
        cmap = 'hot'
        cmap = np.uint8(np.round(255 * plt.get_cmap(cmap)(np.arange(256))))
        self._bgra = np.zeros((Y, X, 4), dtype=np.uint8, order='C')
        self._bgra[..., 0] = cmap[:, 2][image]
        self._bgra[..., 1] = cmap[:, 1][image]
        self._bgra[..., 2] = cmap[:, 0][image]
        return self._bgra

    def to_8bit(self, image):
        return np.round(255 * image).astype('uint8')

    def scale_contrast(self, image, autoscale=False):
        if 1:
            if image.ndim == 2:
                max_ = image.max()
            else:
                max_ = min([_.max() for _ in image])
            upper = self.contrastEdit.value() * max_
        lower = 0
        image = (image - lower) / (upper - lower)
        image[~np.isfinite(image)] = 0
        image = np.minimum(image, 1.0)
        image = np.maximum(image, 0.0)
        return image

def main():

    app = QtGui.QApplication(sys.argv)
    window = Window()
    window.show()

    def excepthook(type, value, tback):
        lib.cancel_dialogs()
        message = ''.join(traceback.format_exception(type, value, tback))
        errorbox = QtGui.QMessageBox.critical(window, 'An error occured', message)
        errorbox.exec_()
        sys.__excepthook__(type, value, tback)
    sys.excepthook = excepthook

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
