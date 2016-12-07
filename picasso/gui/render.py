"""
    gui/render
    ~~~~~~~~~~~~~~~~~~~~

    Graphical user interface for rendering localization images

    :author: Joerg Schnitzbauer, 2016
    :copyright: Copyright (c) 2016 Jungmann Lab, Max Planck Institute of Biochemistry
"""
import os
import os.path
import sys
import traceback
from math import ceil

import lmfit
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import yaml
from matplotlib.backends.backend_qt4agg import (FigureCanvasQTAgg,
                                                NavigationToolbar2QT)
from numpy.lib.recfunctions import stack_arrays
from PyQt4 import QtCore, QtGui

import colorsys

from .. import imageprocess, io, lib, postprocess, render

DEFAULT_OVERSAMPLING = 1.0
INITIAL_REL_MAXIMUM = 0.5
ZOOM = 10 / 7
N_GROUP_COLORS = 8


matplotlib.rcParams.update({'axes.titlesize': 'large'})


def fit_cum_exp(data):
    data.sort()
    n = len(data)
    y = np.arange(1, n + 1)
    data_min = data.min()
    data_max = data.max()
    params = lmfit.Parameters()
    params.add('a', value=n, vary=True, min=0)
    params.add('t', value=np.mean(data), vary=True, min=data_min, max=data_max)
    params.add('c', value=data_min, vary=True, min=0)
    result = lib.CumulativeExponentialModel.fit(y, params, x=data)
    return result


def kinetic_rate_from_fit(data):
    if len(data) > 2:
        if data.ptp() == 0:
            rate = np.nanmean(data)
        else:
            result = fit_cum_exp(data)
            rate = result.best_values['t']
    else:
        rate = np.nanmean(data)
    return rate


estimate_kinetic_rate = kinetic_rate_from_fit  # np.mean


class FloatEdit(QtGui.QLineEdit):

    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QtGui.QSizePolicy.Preferred, QtGui.QSizePolicy.Preferred)
        self.editingFinished.connect(self.onEditingFinished)

    def onEditingFinished(self):
        value = self.value()
        self.valueChanged.emit(value)

    def setValue(self, value):
        text = '{:.10e}'.format(value)
        self.setText(text)

    def value(self):
        text = self.text()
        value = float(text)
        return value


class PickHistWindow(QtGui.QTabWidget):

    def __init__(self, info_dialog):
        super().__init__()
        self.setWindowTitle('Pick Histograms')
        this_directory = os.path.dirname(os.path.realpath(__file__))
        icon_path = os.path.join(this_directory, 'icons', 'render.ico')
        icon = QtGui.QIcon(icon_path)
        self.setWindowIcon(icon)
        self.resize(1000, 400)
        self.figure = plt.Figure()
        self.canvas = FigureCanvasQTAgg(self.figure)
        vbox = QtGui.QVBoxLayout()
        self.setLayout(vbox)
        vbox.addWidget(self.canvas)
        vbox.addWidget((NavigationToolbar2QT(self.canvas, self)))

    def plot(self, pooled_locs, fit_result_len, fit_result_dark):
        self.figure.clear()
        # Length
        axes = self.figure.add_subplot(121)
        axes.set_title('Length (cumulative)')
        data = pooled_locs.len
        data.sort()
        y = np.arange(1, len(data) + 1)
        axes.semilogx(data, y, label='data')
        axes.semilogx(data, fit_result_len.best_fit, label='fit')
        axes.legend(loc='best')
        # Dark
        axes = self.figure.add_subplot(122)
        axes.set_title('Dark time (cumulative)')
        data = pooled_locs.dark
        data.sort()
        y = np.arange(1, len(data) + 1)
        axes.semilogx(data, y, label='data')
        axes.semilogx(data, fit_result_dark.best_fit, label='fit')
        axes.legend(loc='best')
        self.canvas.draw()


class ApplyDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        # vars = self.view.locs[0].dtype.names
        self.setWindowTitle('Apply expression')
        vbox = QtGui.QVBoxLayout(self)
        layout = QtGui.QGridLayout()
        vbox.addLayout(layout)
        layout.addWidget(QtGui.QLabel('Channel:'), 0, 0)
        self.channel = QtGui.QComboBox()
        self.channel.addItems(self.window.view.locs_paths)
        layout.addWidget(self.channel, 0, 1)
        self.channel.currentIndexChanged.connect(self.update_vars)
        layout.addWidget(QtGui.QLabel('Pre-defined variables:'), 1, 0)
        self.label = QtGui.QLabel()
        layout.addWidget(self.label, 1, 1)
        self.update_vars(0)
        layout.addWidget(QtGui.QLabel('Expression:'), 2, 0)
        self.cmd = QtGui.QLineEdit()
        layout.addWidget(self.cmd, 2, 1)
        hbox = QtGui.QHBoxLayout()
        vbox.addLayout(hbox)
        # OK and Cancel buttons
        self.buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel,
                                              QtCore.Qt.Horizontal,
                                              self)
        vbox.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def getCmd(parent=None):
        dialog = ApplyDialog(parent)
        result = dialog.exec_()
        cmd = dialog.cmd.text()
        channel = dialog.channel.currentIndex()
        return (cmd, channel, result == QtGui.QDialog.Accepted)

    def update_vars(self, index):
        vars = self.window.view.locs[index].dtype.names
        self.label.setText(str(vars))


class LinkDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Enter parameters')
        vbox = QtGui.QVBoxLayout(self)
        grid = QtGui.QGridLayout()
        grid.addWidget(QtGui.QLabel('Max. distance (pixels):'), 0, 0)
        self.max_distance = QtGui.QDoubleSpinBox()
        self.max_distance.setRange(0, 1e6)
        self.max_distance.setValue(1)
        grid.addWidget(self.max_distance, 0, 1)
        grid.addWidget(QtGui.QLabel('Max. transient dark frames:'), 1, 0)
        self.max_dark_time = QtGui.QDoubleSpinBox()
        self.max_dark_time.setRange(0, 1e9)
        self.max_dark_time.setValue(1)
        grid.addWidget(self.max_dark_time, 1, 1)
        vbox.addLayout(grid)
        hbox = QtGui.QHBoxLayout()
        vbox.addLayout(hbox)
        # OK and Cancel buttons
        self.buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel,
                                              QtCore.Qt.Horizontal,
                                              self)
        vbox.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    # static method to create the dialog and return input
    @staticmethod
    def getParams(parent=None):
        dialog = LinkDialog(parent)
        result = dialog.exec_()
        return (dialog.max_distance.value(), dialog.max_dark_time.value(), result == QtGui.QDialog.Accepted)


class InfoDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Info')
        self.setModal(False)
        vbox = QtGui.QVBoxLayout(self)
        # Display
        display_groupbox = QtGui.QGroupBox('Display')
        vbox.addWidget(display_groupbox)
        display_grid = QtGui.QGridLayout(display_groupbox)
        display_grid.addWidget(QtGui.QLabel('Width:'), 0, 0)
        self.width_label = QtGui.QLabel()
        display_grid.addWidget(self.width_label, 0, 1)
        display_grid.addWidget(QtGui.QLabel('Height:'), 1, 0)
        self.height_label = QtGui.QLabel()
        display_grid.addWidget(self.height_label, 1, 1)
        # Movie
        movie_groupbox = QtGui.QGroupBox('Movie')
        vbox.addWidget(movie_groupbox)
        self.movie_grid = QtGui.QGridLayout(movie_groupbox)
        self.movie_grid.addWidget(QtGui.QLabel('Median fit precision:'), 0, 0)
        self.fit_precision = QtGui.QLabel('-')
        self.movie_grid.addWidget(self.fit_precision, 0, 1)
        self.movie_grid.addWidget(QtGui.QLabel('NeNA precision:'), 1, 0)
        self.nena_button = QtGui.QPushButton('Calculate')
        self.nena_button.clicked.connect(self.calculate_nena_lp)
        self.nena_button.setDefault(False)
        self.nena_button.setAutoDefault(False)
        self.movie_grid.addWidget(self.nena_button, 1, 1)
        # FOV
        fov_groupbox = QtGui.QGroupBox('Field of view')
        vbox.addWidget(fov_groupbox)
        fov_grid = QtGui.QGridLayout(fov_groupbox)
        fov_grid.addWidget(QtGui.QLabel('# Localizations:'), 0, 0)
        self.locs_label = QtGui.QLabel()
        fov_grid.addWidget(self.locs_label, 0, 1)
        # Picks
        picks_groupbox = QtGui.QGroupBox('Picks')
        vbox.addWidget(picks_groupbox)
        self.picks_grid = QtGui.QGridLayout(picks_groupbox)
        self.picks_grid.addWidget(QtGui.QLabel('# Picks:'), 0, 0)
        self.n_picks = QtGui.QLabel()
        self.picks_grid.addWidget(self.n_picks, 0, 1)
        compute_pick_info_button = QtGui.QPushButton('Calculate info below')
        compute_pick_info_button.clicked.connect(self.window.view.update_pick_info_long)
        self.picks_grid.addWidget(compute_pick_info_button, 1, 0, 1, 3)
        self.picks_grid.addWidget(QtGui.QLabel('<b>Mean</b'), 2, 1)
        self.picks_grid.addWidget(QtGui.QLabel('<b>Std</b>'), 2, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('# Localizations:'), row, 0)
        self.n_localizations_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.n_localizations_mean, row, 1)
        self.n_localizations_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.n_localizations_std, row, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('RMSD to COM:'), row, 0)
        self.rmsd_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.rmsd_mean, row, 1)
        self.rmsd_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.rmsd_std, row, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('RMSD in z:'), row, 0)
        self.rmsd_z_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.rmsd_z_mean, row, 1)
        self.rmsd_z_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.rmsd_z_std, row, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('Ignore dark times <='), row, 0)
        self.max_dark_time = QtGui.QSpinBox()
        self.max_dark_time.setRange(0, 1e9)
        self.max_dark_time.setValue(1)
        self.picks_grid.addWidget(self.max_dark_time, row, 1, 1, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('Length:'), row, 0)
        self.length_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.length_mean, row, 1)
        self.length_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.length_std, row, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('Dark time:'), row, 0)
        self.dark_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.dark_mean, row, 1)
        self.dark_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.dark_std, row, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('# Units per pick:'), row, 0)
        self.units_per_pick = QtGui.QSpinBox()
        self.units_per_pick.setRange(1, 1e6)
        self.units_per_pick.setValue(1)
        self.picks_grid.addWidget(self.units_per_pick, row, 1, 1, 2)
        calculate_influx_button = QtGui.QPushButton('Calibrate influx')
        calculate_influx_button.clicked.connect(self.calibrate_influx)
        self.picks_grid.addWidget(calculate_influx_button, self.picks_grid.rowCount(), 0, 1, 3)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('Influx rate (1/frames):'), row, 0)
        self.influx_rate = FloatEdit()
        self.influx_rate.setValue(0.03)
        self.influx_rate.valueChanged.connect(self.update_n_units)
        self.picks_grid.addWidget(self.influx_rate, row, 1, 1, 2)
        row = self.picks_grid.rowCount()
        self.picks_grid.addWidget(QtGui.QLabel('# Units:'), row, 0)
        self.n_units_mean = QtGui.QLabel()
        self.picks_grid.addWidget(self.n_units_mean, row, 1)
        self.n_units_std = QtGui.QLabel()
        self.picks_grid.addWidget(self.n_units_std, row, 2)
        self.pick_hist_window = PickHistWindow(self)
        pick_hists = QtGui.QPushButton('Histograms')
        pick_hists.clicked.connect(self.pick_hist_window.show)
        self.picks_grid.addWidget(pick_hists, self.picks_grid.rowCount(), 0, 1, 3)

    def calculate_nena_lp(self):
        channel = self.window.view.get_channel('Calculate NeNA precision')
        if channel is not None:
            locs = self.window.view.locs[channel]
            info = self.window.view.infos[channel]
            self.nena_button.setParent(None)
            self.movie_grid.removeWidget(self.nena_button)
            progress = lib.ProgressDialog('Calculating NeNA precision', 0, 100, self)
            result_lp = postprocess.nena(locs, info, progress.set_value)
            self.nena_label = QtGui.QLabel()
            self.movie_grid.addWidget(self.nena_label, 1, 1)
            self.nena_result, lp = result_lp
            self.nena_label.setText('{:.3} pixel'.format(lp))
            show_plot_button = QtGui.QPushButton('Show plot')
            self.movie_grid.addWidget(show_plot_button, self.movie_grid.rowCount() - 1, 2)
            show_plot_button.clicked.connect(self.show_nena_plot)

    def calibrate_influx(self):
        # influx = np.mean(1 / self.pick_info['dark']) / self.units_per_pick.value()
        influx = 1 / self.pick_info['pooled dark'] / self.units_per_pick.value()
        self.influx_rate.setValue(influx)
        self.update_n_units()

    def calculate_n_units(self, dark):
        influx = self.influx_rate.value()
        return 1 / (influx * dark)

    def update_n_units(self):
        n_units = self.calculate_n_units(self.pick_info['dark'])
        self.n_units_mean.setText('{:,.2f}'.format(np.mean(n_units)))
        self.n_units_std.setText('{:,.2f}'.format(np.std(n_units)))

    def show_nena_plot(self):
        d = self.nena_result.userkws['d']
        plt.Figure()
        plt.title('Next frame neighbor distance histogram')
        plt.plot(d, self.nena_result.data, label='Data')
        plt.plot(d, self.nena_result.best_fit, label='Fit')
        plt.legend(loc='best')
        plt.show()


class ToolsSettingsDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Tools Settings')
        self.setModal(False)
        vbox = QtGui.QVBoxLayout(self)
        pick_groupbox = QtGui.QGroupBox('Pick')
        vbox.addWidget(pick_groupbox)
        pick_grid = QtGui.QGridLayout(pick_groupbox)
        pick_grid.addWidget(QtGui.QLabel('Diameter (cam. pixel):'), 0, 0)
        self.pick_diameter = QtGui.QDoubleSpinBox()
        self.pick_diameter.setRange(0, 999999)
        self.pick_diameter.setValue(1)
        self.pick_diameter.setSingleStep(0.1)
        self.pick_diameter.setDecimals(3)
        self.pick_diameter.setKeyboardTracking(False)
        self.pick_diameter.valueChanged.connect(self.on_pick_diameter_changed)
        pick_grid.addWidget(self.pick_diameter, 0, 1)
        pick_grid.addWidget(QtGui.QLabel('Pick similar +/- range (std)'), 1, 0)
        self.pick_similar_range = QtGui.QDoubleSpinBox()
        self.pick_similar_range.setRange(0, 100000)
        self.pick_similar_range.setValue(2)
        self.pick_similar_range.setSingleStep(0.1)
        self.pick_similar_range.setDecimals(1)
        pick_grid.addWidget(self.pick_similar_range, 1, 1)

    def on_pick_diameter_changed(self, diameter):
        self.window.view.index_blocks = [None for _ in self.window.view.index_blocks]
        self.window.view.update_scene(use_cache=True)


class DisplaySettingsDialog(QtGui.QDialog):

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Display Settings')
        self.resize(200, 0)
        self.setModal(False)
        vbox = QtGui.QVBoxLayout(self)
        # General
        general_groupbox = QtGui.QGroupBox('General')
        vbox.addWidget(general_groupbox)
        general_grid = QtGui.QGridLayout(general_groupbox)
        general_grid.addWidget(QtGui.QLabel('Zoom:'), 0, 0)
        self.zoom = QtGui.QDoubleSpinBox()
        self.zoom.setKeyboardTracking(False)
        self.zoom.setRange(10**(-self.zoom.decimals()), 1e6)
        self.zoom.valueChanged.connect(self.on_zoom_changed)
        general_grid.addWidget(self.zoom, 0, 1)
        general_grid.addWidget(QtGui.QLabel('Oversampling:'), 1, 0)
        self._oversampling = DEFAULT_OVERSAMPLING
        self.oversampling = QtGui.QDoubleSpinBox()
        self.oversampling.setRange(0.001, 1000)
        self.oversampling.setSingleStep(5)
        self.oversampling.setValue(self._oversampling)
        self.oversampling.setKeyboardTracking(False)
        self.oversampling.valueChanged.connect(self.on_oversampling_changed)
        general_grid.addWidget(self.oversampling, 1, 1)
        self.dynamic_oversampling = QtGui.QCheckBox('dynamic')
        self.dynamic_oversampling.setChecked(True)
        self.dynamic_oversampling.toggled.connect(self.set_dynamic_oversampling)
        general_grid.addWidget(self.dynamic_oversampling, 2, 1)
        # Contrast
        contrast_groupbox = QtGui.QGroupBox('Contrast')
        vbox.addWidget(contrast_groupbox)
        contrast_grid = QtGui.QGridLayout(contrast_groupbox)
        minimum_label = QtGui.QLabel('Min. Density:')
        contrast_grid.addWidget(minimum_label, 0, 0)
        self.minimum = QtGui.QDoubleSpinBox()
        self.minimum.setRange(0, 999999)
        self.minimum.setSingleStep(5)
        self.minimum.setValue(0)
        self.minimum.setDecimals(3)
        self.minimum.setKeyboardTracking(False)
        self.minimum.valueChanged.connect(self.update_scene)
        contrast_grid.addWidget(self.minimum, 0, 1)
        maximum_label = QtGui.QLabel('Max. Density:')
        contrast_grid.addWidget(maximum_label, 1, 0)
        self.maximum = QtGui.QDoubleSpinBox()
        self.maximum.setRange(0, 999999)
        self.maximum.setSingleStep(5)
        self.maximum.setValue(100)
        self.maximum.setDecimals(3)
        self.maximum.setKeyboardTracking(False)
        self.maximum.valueChanged.connect(self.update_scene)
        contrast_grid.addWidget(self.maximum, 1, 1)
        contrast_grid.addWidget(QtGui.QLabel('Colormap:'), 2, 0)
        self.colormap = QtGui.QComboBox()
        self.colormap.addItems(sorted(['hot', 'viridis', 'inferno', 'plasma', 'magma', 'gray']))
        contrast_grid.addWidget(self.colormap, 2, 1)
        self.colormap.currentIndexChanged.connect(self.update_scene)
        # Blur
        blur_groupbox = QtGui.QGroupBox('Blur')
        blur_grid = QtGui.QGridLayout(blur_groupbox)
        self.blur_buttongroup = QtGui.QButtonGroup()
        points_button = QtGui.QRadioButton('None')
        self.blur_buttongroup.addButton(points_button)
        smooth_button = QtGui.QRadioButton('One-Pixel-Blur')
        self.blur_buttongroup.addButton(smooth_button)
        convolve_button = QtGui.QRadioButton('Global Localization Precision')
        self.blur_buttongroup.addButton(convolve_button)
        gaussian_button = QtGui.QRadioButton('Individual Localization Precision')
        self.blur_buttongroup.addButton(gaussian_button)
        blur_grid.addWidget(points_button, 0, 0, 1, 2)
        blur_grid.addWidget(smooth_button, 1, 0, 1, 2)
        blur_grid.addWidget(convolve_button, 2, 0, 1, 2)
        blur_grid.addWidget(gaussian_button, 3, 0, 1, 2)
        convolve_button.setChecked(True)
        self.blur_buttongroup.buttonReleased.connect(self.render_scene)
        blur_grid.addWidget(QtGui.QLabel('Min. Blur (cam. pixel):'), 4, 0, 1, 1)
        self.min_blur_width = QtGui.QDoubleSpinBox()
        self.min_blur_width.setRange(0, 999999)
        self.min_blur_width.setSingleStep(0.01)
        self.min_blur_width.setValue(0)
        self.min_blur_width.setDecimals(3)
        self.min_blur_width.setKeyboardTracking(False)
        self.min_blur_width.valueChanged.connect(self.render_scene)
        blur_grid.addWidget(self.min_blur_width, 4, 1, 1, 1)
        vbox.addWidget(blur_groupbox)
        self.blur_methods = {points_button: None, smooth_button: 'smooth',
                             convolve_button: 'convolve', gaussian_button: 'gaussian'}
        # Scale bar
        self.scalebar_groupbox = QtGui.QGroupBox('Scale Bar')
        self.scalebar_groupbox.setCheckable(True)
        self.scalebar_groupbox.setChecked(False)
        self.scalebar_groupbox.toggled.connect(self.update_scene)
        vbox.addWidget(self.scalebar_groupbox)
        scalebar_grid = QtGui.QGridLayout(self.scalebar_groupbox)
        scalebar_grid.addWidget(QtGui.QLabel('Pixel Size:'), 0, 0)
        self.pixelsize = QtGui.QDoubleSpinBox()
        self.pixelsize.setRange(1, 1000000000)
        self.pixelsize.setValue(160)
        self.pixelsize.setKeyboardTracking(False)
        self.pixelsize.valueChanged.connect(self.update_scene)
        scalebar_grid.addWidget(self.pixelsize, 0, 1)
        scalebar_grid.addWidget(QtGui.QLabel('Scale Bar Length (nm):'), 1, 0)
        self.scalebar = QtGui.QDoubleSpinBox()
        self.scalebar.setRange(0.0001, 10000000000)
        self.scalebar.setValue(500)
        self.scalebar.setKeyboardTracking(False)
        self.scalebar.valueChanged.connect(self.update_scene)
        scalebar_grid.addWidget(self.scalebar, 1, 1)
        self._silent_oversampling_update = False

    def on_oversampling_changed(self, value):
        contrast_factor = (self._oversampling / value)**2
        self._oversampling = value
        self.silent_minimum_update(contrast_factor * self.minimum.value())
        self.silent_maximum_update(contrast_factor * self.maximum.value())
        if not self._silent_oversampling_update:
            self.dynamic_oversampling.setChecked(False)
            self.window.view.update_scene()

    def on_zoom_changed(self, value):
        self.window.view.set_zoom(value)

    def set_oversampling_silently(self, oversampling):
        self._silent_oversampling_update = True
        self.oversampling.setValue(oversampling)
        self._silent_oversampling_update = False

    def set_zoom_silently(self, zoom):
        self.zoom.blockSignals(True)
        self.zoom.setValue(zoom)
        self.zoom.blockSignals(False)

    def silent_minimum_update(self, value):
        self.minimum.blockSignals(True)
        self.minimum.setValue(value)
        self.minimum.blockSignals(False)

    def silent_maximum_update(self, value):
        self.maximum.blockSignals(True)
        self.maximum.setValue(value)
        self.maximum.blockSignals(False)

    def render_scene(self, *args, **kwargs):
        self.window.view.update_scene()

    def set_dynamic_oversampling(self, state):
        if state:
            self.window.view.update_scene()

    def update_scene(self, *args, **kwargs):
        self.window.view.update_scene(use_cache=True)


class View(QtGui.QLabel):

    def __init__(self, window):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Expanding)
        self.rubberband = QtGui.QRubberBand(QtGui.QRubberBand.Rectangle, self)
        self.rubberband.setStyleSheet('selection-background-color: white')
        self.window = window
        self._pixmap = None
        self.locs = []
        self.infos = []
        self.locs_paths = []
        self._mode = 'Zoom'
        self._pan = False
        self._size_hint = (768, 768)
        self.n_locs = 0
        self._picks = []
        self.index_blocks = []
        self._drift = []

    def add(self, path, render=True):
        try:
            locs, info = io.load_locs(path, qt_parent=self)
        except io.NoMetadataFileError:
            return
        locs = lib.ensure_sanity(locs, info)
        self.locs.append(locs)
        self.infos.append(info)
        self.locs_paths.append(path)
        self.index_blocks.append(None)
        self._drift.append(None)
        if len(self.locs) == 1:
            self.median_lp = np.mean([np.median(locs.lpx), np.median(locs.lpy)])
            if hasattr(locs, 'group'):
                groups = np.unique(locs.group)
                np.random.shuffle(groups)
                groups %= N_GROUP_COLORS
                self.group_color = groups[locs.group]
            if render:
                self.fit_in_view(autoscale=True)
        else:
            if render:
                self.update_scene()
        os.chdir(os.path.dirname(path))

    def add_multiple(self, paths):
        fit_in_view = len(self.locs) == 0
        paths = sorted(paths)
        for path in paths:
            self.add(path, render=False)
        if len(self.locs):  # In case loading was not succesful.
            if fit_in_view:
                self.fit_in_view(autoscale=True)
            else:
                self.update_scene()

    def add_pick(self, position, update_scene=True):
        self._picks.append(position)
        self.update_pick_info_short()
        if update_scene:
            self.update_scene(picks_only=True)

    def add_picks(self, positions):
        for position in positions:
            self.add_pick(position, update_scene=False)
        self.update_scene(picks_only=True)

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

    def align(self):
        if len(self._picks) > 0:
            shift = self.shift_from_picked()
        else:
            shift = self.shift_from_rcc()
        sp = lib.ProgressDialog('Shifting channels', 0, len(self.locs), self)
        sp.set_value(0)
        for i, locs_ in enumerate(self.locs):
            locs_.y -= shift[0][i]
            locs_.x -= shift[1][i]
            if len(shift) == 3:
                locs_.z -= shift[2][i]
            sp.set_value(i+1)
        self.update_scene()

    def combine(self):
        if len(self._picks) > 0:
            channel = self.get_channel()
            picked_locs = self.picked_locs(channel, add_group=False)
            out_locs = []
            r_max = 2 * max(self.infos[channel][0]['Height'], self.infos[channel][0]['Width'])
            max_dark = self.infos[channel][0]['Frames']
            progress = lib.ProgressDialog('Combining localizations in picks', 0, len(picked_locs), self)
            progress.set_value(0)
            for i, pick_locs in enumerate(picked_locs):
                pick_locs_out = postprocess.link(pick_locs, self.infos[channel], r_max=r_max, max_dark_time=max_dark, remove_ambiguous_lengths=False)
                out_locs.append(pick_locs_out)
                progress.set_value(i+1)
            self.locs[channel] = stack_arrays(out_locs, asrecarray=True, usemask=False)
            self.update_scene()

    def link(self):
        channel = self.get_channel()
        if hasattr(self.locs[channel], 'len'):
            QtGui.QMessageBox.information(self, 'Link', 'Localizations are already linked. Aborting...')
            return
        else:
            r_max, max_dark, ok = LinkDialog.getParams()
            if ok:
                status = lib.StatusDialog('Linking localizations...', self)
                self.locs[channel] = postprocess.link(self.locs[channel], self.infos[channel], r_max=r_max, max_dark_time=max_dark)
                status.close()
                self.update_scene()

    def shifts_from_picked_coordinate(self, locs, coordinate):
        ''' Calculates the shift from each channel to each other along a given coordinate. '''
        n_channels = len(locs)
        # Calculating center of mass for each channel and pick
        coms = []
        for channel_locs in locs:
            coms.append([])
            for group_locs in channel_locs:
                group_com = np.mean(getattr(group_locs, coordinate))
                coms[-1].append(group_com)
        # Calculating image shifts
        d = np.zeros((n_channels, n_channels))
        for i in range(n_channels-1):
            for j in range(i+1, n_channels):
                    d[i, j] = np.nanmean([cj - ci for ci, cj in zip(coms[i], coms[j])])
        return d

    def shift_from_picked(self):
        ''' Used by align. For each pick, calculate the center of mass and does rcc based on shifts '''
        n_channels = len(self.locs)
        locs = [self.picked_locs(_) for _ in range(n_channels)]
        dy = self.shifts_from_picked_coordinate(locs, 'y')
        dx = self.shifts_from_picked_coordinate(locs, 'x')
        if all([hasattr(_[0], 'z') for _ in locs]):
            dz = self.shifts_from_picked_coordinate(locs, 'z')
        else:
            dz = None
        return lib.minimize_shifts(dx, dy, shifts_z=dz)

    def shift_from_rcc(self):
        ''' Used by align. Estimates image shifts based on image correlation. '''
        n_channels = len(self.locs)
        rp = lib.ProgressDialog('Rendering images', 0, n_channels, self)
        rp.set_value(0)
        images = []
        for i, (locs_, info_) in enumerate(zip(self.locs, self.infos)):
            _, image = render.render(locs_, info_, blur_method='smooth')
            images.append(image)
            rp.set_value(i + 1)
        n_pairs = int(n_channels * (n_channels - 1) / 2)
        rc = lib.ProgressDialog('Correlating image pairs', 0, n_pairs, self)
        return imageprocess.rcc(images, callback=rc.set_value)

    def clear_picks(self):
        self._picks = []
        self.update_scene(picks_only=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def draw_picks(self, image):
        image = image.copy()
        d = self.window.tools_settings_dialog.pick_diameter.value()
        d *= self.width() / self.viewport_width()
        # d = int(round(d))
        painter = QtGui.QPainter(image)
        painter.setPen(QtGui.QColor('yellow'))
        for pick in self._picks:
            cx, cy = self.map_to_view(*pick)
            painter.drawEllipse(cx - d / 2, cy - d / 2, d, d)
        painter.end()
        return image

    def draw_scalebar(self, image):
        if self.window.display_settings_dialog.scalebar_groupbox.isChecked():
            pixelsize = self.window.display_settings_dialog.pixelsize.value()
            scalebar = self.window.display_settings_dialog.scalebar.value()
            length_camerapxl = scalebar / pixelsize
            length_displaypxl = int(round(self.width() * length_camerapxl / self.viewport_width()))
            height = max(int(round(0.15 * length_displaypxl)), 1)
            painter = QtGui.QPainter(image)
            painter.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            painter.setBrush(QtGui.QBrush(QtGui.QColor('white')))
            x = self.width() - length_displaypxl - 20
            y = self.height() - height - 20
            painter.drawRect(x, y, length_displaypxl + 0, height + 0)
        return image

    def draw_scene(self, viewport, autoscale=False, use_cache=False, picks_only=False):
        if not picks_only:
            self.viewport = self.adjust_viewport_to_view(viewport)
            qimage = self.render_scene(autoscale=autoscale, use_cache=use_cache)
            qimage = qimage.scaled(self.width(), self.height(), QtCore.Qt.KeepAspectRatioByExpanding)
            self.qimage_no_picks = self.draw_scalebar(qimage)
            dppvp = self.display_pixels_per_viewport_pixels()
            self.window.display_settings_dialog.set_zoom_silently(dppvp)
        self.qimage = self.draw_picks(self.qimage_no_picks)
        pixmap = QtGui.QPixmap.fromImage(self.qimage)
        self.setPixmap(pixmap)
        self.window.update_info()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        paths = [_.toLocalFile() for _ in urls]
        extensions = [os.path.splitext(_)[1].lower() for _ in paths]
        paths = [path for path, ext in zip(paths, extensions) if ext == '.hdf5']
        self.add_multiple(paths)

    def fit_in_view(self, autoscale=False):
        movie_height, movie_width = self.movie_size()
        viewport = [(0, 0), (movie_height, movie_width)]
        self.update_scene(viewport=viewport, autoscale=autoscale)

    def get_channel(self, title='Choose a channel'):
        n_channels = len(self.locs_paths)
        if n_channels == 0:
            return None
        elif n_channels == 1:
            return 0
        elif len(self.locs_paths) > 1:
            index, ok = QtGui.QInputDialog.getItem(self, 'Save localizations', 'Channel:', self.locs_paths, editable=False)
            if ok:
                return self.locs_paths.index(index)
            else:
                return None

    def get_render_kwargs(self, viewport=None):
        ''' Returns a dictionary to be used for the keyword arguments of render. '''
        blur_button = self.window.display_settings_dialog.blur_buttongroup.checkedButton()
        optimal_oversampling = self.display_pixels_per_viewport_pixels()
        if self.window.display_settings_dialog.dynamic_oversampling.isChecked():
            oversampling = optimal_oversampling
            self.window.display_settings_dialog.set_oversampling_silently(optimal_oversampling)
        else:
            oversampling = float(self.window.display_settings_dialog.oversampling.value())
            if oversampling > optimal_oversampling:
                QtGui.QMessageBox.information(self,
                                              'Oversampling too high',
                                              'Oversampling will be adjusted to match the display pixel density.')
                oversampling = optimal_oversampling
                self.window.display_settings_dialog.set_oversampling_silently(optimal_oversampling)
        if viewport is None:
            viewport = self.viewport
        return {'oversampling': oversampling,
                'viewport': viewport,
                'blur_method': self.window.display_settings_dialog.blur_methods[blur_button],
                'min_blur_width': float(self.window.display_settings_dialog.min_blur_width.value())}

    def load_picks(self, path):
        ''' Loads picks centers and diameter from yaml file. '''
        with open(path, 'r') as f:
            regions = yaml.load(f)
        self._picks = regions['Centers']
        self.update_pick_info_short()
        self.window.tools_settings_dialog.pick_diameter.setValue(regions['Diameter'])
        self.update_scene(picks_only=True)

    def map_to_movie(self, position):
        ''' Converts coordinates from display units to camera units. '''
        x_rel = position.x() / self.width()
        x_movie = x_rel * self.viewport_width() + self.viewport[0][1]
        y_rel = position.y() / self.height()
        y_movie = y_rel * self.viewport_height() + self.viewport[0][0]
        return x_movie, y_movie

    def map_to_view(self, x, y, z=None):
        ''' Converts coordinates from camera units to display units. '''
        cx = self.width() * (x - self.viewport[0][1]) / self.viewport_width()
        cy = self.height() * (y - self.viewport[0][0]) / self.viewport_height()
        return cx, cy

    def max_movie_height(self):
        ''' Returns maximum height of all loaded images. '''
        return max(info[0]['Height'] for info in self.infos)

    def max_movie_width(self):
        return max([info[0]['Width'] for info in self.infos])

    def mouseMoveEvent(self, event):
        if self._mode == 'Zoom':
            if self.rubberband.isVisible():
                self.rubberband.setGeometry(QtCore.QRect(self.origin, event.pos()))
            if self._pan:
                rel_x_move = (event.x() - self.pan_start_x) / self.width()
                rel_y_move = (event.y() - self.pan_start_y) / self.height()
                self.pan_relative(rel_y_move, rel_x_move)
                self.pan_start_x = event.x()
                self.pan_start_y = event.y()

    def mousePressEvent(self, event):
        if self._mode == 'Zoom':
            if event.button() == QtCore.Qt.LeftButton:
                if not self.rubberband.isVisible():
                    self.origin = QtCore.QPoint(event.pos())
                    self.rubberband.setGeometry(QtCore.QRect(self.origin, QtCore.QSize()))
                    self.rubberband.show()
            elif event.button() == QtCore.Qt.RightButton:
                self._pan = True
                self.pan_start_x = event.x()
                self.pan_start_y = event.y()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                event.accept()
            else:
                event.ignore()

    def mouseReleaseEvent(self, event):
        if self._mode == 'Zoom':
            if event.button() == QtCore.Qt.LeftButton and self.rubberband.isVisible():
                end = QtCore.QPoint(event.pos())
                if end.x() > self.origin.x() and end.y() > self.origin.y():
                    x_min_rel = self.origin.x() / self.width()
                    x_max_rel = end.x() / self.width()
                    y_min_rel = self.origin.y() / self.height()
                    y_max_rel = end.y() / self.height()
                    viewport_height, viewport_width = self.viewport_size()
                    x_min = self.viewport[0][1] + x_min_rel * viewport_width
                    x_max = self.viewport[0][1] + x_max_rel * viewport_width
                    y_min = self.viewport[0][0] + y_min_rel * viewport_height
                    y_max = self.viewport[0][0] + y_max_rel * viewport_height
                    viewport = [(y_min, x_min), (y_max, x_max)]
                    self.update_scene(viewport)
                self.rubberband.hide()
            elif event.button() == QtCore.Qt.RightButton:
                self._pan = False
                self.setCursor(QtCore.Qt.ArrowCursor)
                event.accept()
            else:
                event.ignore()
        elif self._mode == 'Pick':
            if event.button() == QtCore.Qt.LeftButton:
                x, y = self.map_to_movie(event.pos())
                self.add_pick((x, y))
                event.accept()
            elif event.button() == QtCore.Qt.RightButton:
                x, y = self.map_to_movie(event.pos())
                self.remove_picks((x, y))
                event.accept()
            else:
                event.ignore()

    def movie_size(self):
        movie_height = self.max_movie_height()
        movie_width = self.max_movie_width()
        return (movie_height, movie_width)

    def display_pixels_per_viewport_pixels(self):
        os_horizontal = self.width() / self.viewport_width()
        os_vertical = self.height() / self.viewport_height()
        # The values should be identical, but just in case, we choose the max:
        return max(os_horizontal, os_vertical)

    def pan_relative(self, dy, dx):
        viewport_height, viewport_width = self.viewport_size()
        x_move = dx * viewport_width
        y_move = dy * viewport_height
        x_min = self.viewport[0][1] - x_move
        x_max = self.viewport[1][1] - x_move
        y_min = self.viewport[0][0] - y_move
        y_max = self.viewport[1][0] - y_move
        viewport = [(y_min, x_min), (y_max, x_max)]
        self.update_scene(viewport)

    def rmsd_at_com(self, locs):
        com_x = locs.x.mean()
        com_y = locs.y.mean()
        return np.sqrt(np.mean((locs.x - com_x)**2 + (locs.y - com_y)**2))

    def index_locs(self, channel):
        ''' Indexes localizations in a grid with grid size equal to the pick radius. '''
        locs = self.locs[channel]
        info = self.infos[channel]
        d = self.window.tools_settings_dialog.pick_diameter.value()
        size = d / 2
        K, L = postprocess.index_blocks_shape(info, size)
        progress = lib.ProgressDialog('Indexing localizations', 0, K, self)
        progress.show()
        progress.set_value(0)
        index_blocks = postprocess.get_index_blocks(locs, info, size, progress.set_value)
        self.index_blocks[channel] = index_blocks

    def get_index_blocks(self, channel):
        if self.index_blocks[channel] is None:
            self.index_locs(channel)
        return self.index_blocks[channel]

    def pick_similar(self):
        channel = self.get_channel('Pick similar')
        if channel is not None:
            locs = self.locs[channel]
            info = self.infos[channel]
            d = self.window.tools_settings_dialog.pick_diameter.value()
            r = d / 2
            std_range = self.window.tools_settings_dialog.pick_similar_range.value()
            index_blocks = self.get_index_blocks(channel)
            n_locs = []
            rmsd = []
            has_z = hasattr(locs, 'z')
            if has_z:
                rmsd_z = []
                z_similar = np.empty(len(self._picks))
            for i, pick in enumerate(self._picks):
                x = pick[0]
                y = pick[1]
                block_locs = postprocess.get_block_locs_at(x, y, index_blocks)
                pick_locs = lib.locs_at(x, y, block_locs, r)
                n_locs.append(len(pick_locs))
                rmsd.append(self.rmsd_at_com(pick_locs))
                if has_z:
                    rmsd_z.append(np.std(pick_locs.z))
                    z_similar[i] = np.mean(pick_locs.z)
            mean_n_locs = np.mean(n_locs)
            mean_rmsd = np.mean(rmsd)
            std_n_locs = np.std(n_locs)
            std_rmsd = np.std(rmsd)
            min_n_locs = mean_n_locs - std_range * std_n_locs
            max_n_locs = mean_n_locs + std_range * std_n_locs
            min_rmsd = mean_rmsd - std_range * std_rmsd
            max_rmsd = mean_rmsd + std_range * std_rmsd
            # x, y coordinates of found similar regions:
            x_similar = np.array([_[0] for _ in self._picks])
            y_similar = np.array([_[1] for _ in self._picks])
            # preparations for grid search
            x_range = np.arange(0, info[0]['Width'] + d / 2, d / 2)
            y_range = np.arange(0, info[0]['Height'] + d / 2, d / 2)
            if has_z:
                mean_rmsd_z = np.mean(rmsd_z)
                self._pick_similar_z_radius = 2*mean_rmsd_z
                std_rmsd_z = np.std(rmsd_z)
                min_rmsd_z = mean_rmsd_z - std_range * std_rmsd_z
                max_rmsd_z = mean_rmsd_z + std_range * std_rmsd_z
                mean_z = np.mean(locs.z)
                std_z = np.std(locs.z)
                min_z = mean_z - 2*std_z
                max_z = mean_z + 2*std_z + self._pick_similar_z_radius
                z_range = np.arange(min_z, max_z, self._pick_similar_z_radius)
                r2 = r**2
                rz2 = self._pick_similar_z_radius**2
                dz2 = 4*rz2
            d2 = d**2
            nx = len(x_range)
            locs, size, x_index, y_index, block_starts, block_ends, K, L = index_blocks
            progress = lib.ProgressDialog('Pick similar', 0, nx, self)
            progress.set_value(0)
            for i, x_grid in enumerate(x_range):
                for y_grid in y_range:
                    n_block_locs = postprocess.n_block_locs_at(x_grid, y_grid, size, K, L, block_starts, block_ends)
                    if n_block_locs > 1:
                        block_locs = postprocess.get_block_locs_at(x_grid, y_grid, index_blocks)
                        picked_locs = lib.locs_at(x_grid, y_grid, block_locs, r)
                        if len(picked_locs) > 1:
                            if has_z:
                                for z_grid in z_range:
                                    # pick only the locs that are in the ellipsoid
                                    picked = (((picked_locs.x - x_grid)**2 + (picked_locs.y - y_grid)**2)/r2 + (picked_locs.z - z_grid)**2/rz2) < 1
                                    picked_locs_z = picked_locs[picked]
                                    if len(picked_locs_z) > 1:
                                        # Move COM
                                        x_test_old = x_grid
                                        y_test_old = y_grid
                                        z_test_old = z_grid
                                        x_test = picked_locs_z.x.mean()
                                        y_test = picked_locs_z.y.mean()
                                        z_test = picked_locs_z.z.mean()
                                        while np.abs(x_test - x_test_old) > 1e-3 or np.abs(y_test - y_test_old) > 1e-3 or np.abs(z_test - z_test_old) > 1e-3:
                                            x_test_old = x_test
                                            y_test_old = y_test
                                            z_test_old = z_test
                                            picked_locs = lib.locs_at(x_test, y_test, block_locs, r)
                                            picked = (((picked_locs.x - x_test)**2 + (picked_locs.y - y_test)**2)/r2 + (picked_locs.z - z_test)**2/rz2) < 1
                                            picked_locs_z = picked_locs[picked]
                                            x_test = picked_locs_z.x.mean()
                                            y_test = picked_locs_z.y.mean()
                                            z_test = picked_locs_z.z.mean()
                                        # Check if there is an overlap with another already picked region
                                        if min_n_locs < len(picked_locs_z) < max_n_locs:
                                            if min_rmsd < self.rmsd_at_com(picked_locs_z) < max_rmsd:
                                                if min_rmsd_z < np.std(picked_locs_z.z) < max_rmsd_z:
                                                    if np.all(((x_similar - x_test)**2 + (y_similar - y_test)**2)/d2 + (z_similar - z_test)**2/dz2 > 1):
                                                        x_similar = np.append(x_similar, x_test)
                                                        y_similar = np.append(y_similar, y_test)
                                                        z_similar = np.append(z_similar, z_test)
                            else:
                                # Move to COM peak
                                x_test_old = x_grid
                                y_test_old = y_grid
                                x_test = picked_locs.x.mean()
                                y_test = picked_locs.y.mean()
                                while np.abs(x_test - x_test_old) > 1e-3 or np.abs(y_test - y_test_old) > 1e-3:
                                    x_test_old = x_test
                                    y_test_old = y_test
                                    picked_locs = lib.locs_at(x_test, y_test, block_locs, r)
                                    x_test = picked_locs.x.mean()
                                    y_test = picked_locs.y.mean()
                                # Check for overlaps with other already picked regions
                                if min_n_locs < len(picked_locs) < max_n_locs:
                                    if np.all((x_similar - x_test)**2 + (y_similar - y_test)**2 > d2):
                                        if min_rmsd < self.rmsd_at_com(picked_locs) < max_rmsd:
                                            x_similar = np.append(x_similar, x_test)
                                            y_similar = np.append(y_similar, y_test)
                progress.set_value(i + 1)
            if has_z:
                similar = list(zip(x_similar, y_similar, z_similar))
            else:
                similar = list(zip(x_similar, y_similar))
            self._picks = []
            self.add_picks(similar)

    def picked_locs(self, channel, add_group=True):
        ''' Returns picked localizations in the specified channel '''
        if len(self._picks):
            d = self.window.tools_settings_dialog.pick_diameter.value()
            r = d / 2
            index_blocks = self.get_index_blocks(channel)
            picked_locs = []
            progress = lib.ProgressDialog('Creating localization list', 0, len(self._picks), self)
            progress.set_value(0)
            for i, pick in enumerate(self._picks):
                x = pick[0]
                y = pick[1]
                block_locs = postprocess.get_block_locs_at(x, y, index_blocks)
                group_locs = lib.locs_at(x, y, block_locs, r)
                if add_group:
                    group = i * np.ones(len(group_locs), dtype=np.int32)
                    group_locs = lib.append_to_rec(group_locs, group, 'group')
                picked_locs.append(group_locs)
                progress.set_value(i + 1)
            return picked_locs

    def remove_picks(self, position):
        x, y = position
        pick_diameter_2 = self.window.tools_settings_dialog.pick_diameter.value()**2
        new_picks = []
        for pick in self._picks:
            d2 = (x - pick[0])**2 + (y - pick[1])**2
            if d2 > pick_diameter_2:
                new_picks.append(pick)
        self._picks = []
        self.add_picks(new_picks)

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
        cmap = self.window.display_settings_dialog.colormap.currentText()
        cmap = np.uint8(np.round(255 * plt.get_cmap(cmap)(np.arange(256))))
        self._bgra = np.zeros((Y, X, 4), dtype=np.uint8, order='C')
        self._bgra[..., 0] = cmap[:, 2][image]
        self._bgra[..., 1] = cmap[:, 1][image]
        self._bgra[..., 2] = cmap[:, 0][image]
        return self._bgra

    def resizeEvent(self, event):
        self.update_scene()

    def save_picked_locs(self, path, channel):
        locs = self.picked_locs(channel)
        locs = stack_arrays(locs, asrecarray=True, usemask=False)
        if locs is not None:
            d = self.window.tools_settings_dialog.pick_diameter.value()
            pick_info = {'Generated by:': 'Picasso Render', 'Pick Diameter:': d}
            io.save_locs(path, locs, self.infos[channel] + [pick_info])

    def save_pick_properties(self, path, channel):
        picked_locs = self.picked_locs(channel)
        pick_diameter = self.window.tools_settings_dialog.pick_diameter.value()
        r_max = min(pick_diameter, 1)
        max_dark = self.window.info_dialog.max_dark_time.value()
        out_locs = []
        progress = lib.ProgressDialog('Calculating kinetics', 0, len(picked_locs), self)
        progress.set_value(0)
        dark = np.empty(len(picked_locs))
        for i, pick_locs in enumerate(picked_locs):
            if not hasattr(pick_locs, 'len'):
                pick_locs = postprocess.link(pick_locs, self.infos[channel], r_max=r_max, max_dark_time=max_dark)
            pick_locs = postprocess.compute_dark_times(pick_locs)
            out_locs.append(pick_locs)
            dark[i] = estimate_kinetic_rate(pick_locs.dark)
            progress.set_value(i + 1)
        out_locs = stack_arrays(out_locs, asrecarray=True, usemask=False)
        n_groups = len(picked_locs)
        progress = lib.ProgressDialog('Calculating pick properties', 0, n_groups, self)
        pick_props = postprocess.groupprops(out_locs)
        n_units = self.window.info_dialog.calculate_n_units(dark)
        pick_props = lib.append_to_rec(pick_props, n_units, 'n_units')
        influx = self.window.info_dialog.influx_rate.value()
        info = self.infos[channel] + [{'Generated by': 'Picasso: Render',
                                       'Influx rate': influx}]
        io.save_datasets(path, info, groups=pick_props)

    def save_picks(self, path):
        d = self.window.tools_settings_dialog.pick_diameter.value()
        picks = {'Diameter': d, 'Centers': [list(_) for _ in self._picks]}
        with open(path, 'w') as f:
            yaml.dump(picks, f)

    def scale_contrast(self, image, autoscale=False):
        if autoscale:
            if image.ndim == 2:
                max_ = image.max()
            else:
                max_ = min([_.max() for _ in image])
            upper = INITIAL_REL_MAXIMUM * max_
            self.window.display_settings_dialog.silent_minimum_update(0)
            self.window.display_settings_dialog.silent_maximum_update(upper)
        upper = self.window.display_settings_dialog.maximum.value()
        lower = self.window.display_settings_dialog.minimum.value()
        image = (image - lower) / (upper - lower)
        image[~np.isfinite(image)] = 0
        image = np.minimum(image, 1.0)
        image = np.maximum(image, 0.0)
        return image

    def set_mode(self, action):
        self._mode = action.text()
        self.update_cursor()

    def set_zoom(self, zoom):
        current_zoom = self.display_pixels_per_viewport_pixels()
        self.zoom(current_zoom / zoom)

    def sizeHint(self):
        return QtCore.QSize(*self._size_hint)

    def to_8bit(self, image):
        return np.round(255 * image).astype('uint8')

    def to_left(self):
        self.pan_relative(0, 0.8)

    def to_right(self):
        self.pan_relative(0, -0.8)

    def to_up(self):
        self.pan_relative(0.8, 0)

    def to_down(self):
        self.pan_relative(-0.8, 0)

    def add_drift(self, channel, drift):
        if self._drift[channel] is None:
            self._drift[channel] = drift
        else:
            self._drift[channel].x += drift.x
            self._drift[channel].y += drift.y
        base, ext = os.path.splitext(self.locs_paths[channel])
        np.savetxt(base + '_drift.txt', self._drift[channel], header='dx\tdy', newline='\r\n')

    def undrift(self):
        ''' Undrifts with rcc. '''
        channel = self.get_channel('Undrift')
        if channel is not None:
            segmentation, ok = QtGui.QInputDialog.getInt(self, 'Undrift by RCC', 'Segmentation:', 1000)
            if ok:
                locs = self.locs[channel]
                info = self.infos[channel]
                n_segments = render.n_segments(info, segmentation)
                seg_progress = lib.ProgressDialog('Generating segments', 0, n_segments, self)
                n_pairs = int(n_segments * (n_segments - 1) / 2)
                rcc_progress = lib.ProgressDialog('Correlating image pairs', 0, n_pairs, self)
                drift, _ = postprocess.undrift(locs, info, segmentation, True, seg_progress.set_value, rcc_progress.set_value)
                self.locs[channel] = lib.ensure_sanity(locs, info)
                self.index_blocks[channel] = None
                self.add_drift(channel, drift)
                self.update_scene()

    def undrift_from_picked(self):
        channel = self.get_channel('Undrift from picked')
        if channel is not None:
            self._undrift_from_picked(channel)

    def _undrift_from_picked_coordinate(self, channel, picked_locs, coordinate):
        n_picks = len(picked_locs)
        n_frames = self.infos[channel][0]['Frames']

        # Drift per pick per frame
        drift = np.empty((n_picks, n_frames))
        drift.fill(np.nan)

        # Remove center of mass offset
        for i, locs in enumerate(picked_locs):
            coordinates = getattr(locs, coordinate)
            drift[i, locs.frame] = coordinates - np.mean(coordinates)

        # Mean drift over picks
        drift_mean = np.nanmean(drift, 0)
        # Square deviation of each pick's drift to mean drift along frames
        sd = (drift - drift_mean)**2
        # Mean of square deviation for each pick
        msd = np.nanmean(sd, 1)
        # New mean drift over picks, where each pick is weighted according to its msd
        nan_mask = np.isnan(drift)
        drift = np.ma.MaskedArray(drift, mask=nan_mask)
        drift_mean = np.ma.average(drift, axis=0, weights=1/msd)
        drift_mean = drift_mean.filled(np.nan)

        # Linear interpolation for frames without localizations
        def nan_helper(y):
            return np.isnan(y), lambda z: z.nonzero()[0]
        nans, nonzero = nan_helper(drift_mean)
        drift_mean[nans] = np.interp(nonzero(nans), nonzero(~nans), drift_mean[~nans])

        return drift_mean

    def _undrift_from_picked(self, channel):
        picked_locs = self.picked_locs(channel)
        status = lib.StatusDialog('Calculating drift...', self)

        drift_x = self._undrift_from_picked_coordinate(channel, picked_locs, 'x')
        drift_y = self._undrift_from_picked_coordinate(channel, picked_locs, 'y')

        # Apply drift
        self.locs[channel].x -= drift_x[self.locs[channel].frame]
        self.locs[channel].y -= drift_y[self.locs[channel].frame]

        # A rec array to store the applied drift
        drift = (drift_x, drift_y)
        drift = np.rec.array(drift, dtype=[('x', 'f'), ('y', 'f')])

        # If z coordinate exists, also apply drift there
        if all([hasattr(_, 'z') for _ in picked_locs]):
            drift_z = self._undrift_from_picked_coordinate(channel, picked_locs, 'z')
            self.locs[channel].z -= drift_z[self.locs[channel].frame]
            drift = lib.append_to_rec(drift, drift_z, 'z')

        # Cleanup
        self.index_blocks[channel] = None
        self.add_drift(channel, drift)
        status.close()
        self.update_scene()

    def update_cursor(self):
        if self._mode == 'Zoom':
            self.unsetCursor()
        elif self._mode == 'Pick':
            diameter = self.window.tools_settings_dialog.pick_diameter.value()
            diameter = self.width() * diameter / self.viewport_width()
            pixmap_size = ceil(diameter)
            pixmap = QtGui.QPixmap(pixmap_size, pixmap_size)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setPen(QtGui.QColor('white'))
            offset = (pixmap_size - diameter) / 2
            painter.drawEllipse(offset, offset, diameter, diameter)
            painter.end()
            cursor = QtGui.QCursor(pixmap)
            self.setCursor(cursor)

    def update_pick_info_long(self, info):
        ''' Gets called when "Show info below" '''
        channel = self.get_channel('Calculate pick info')
        if channel is not None:
            d = self.window.tools_settings_dialog.pick_diameter.value()
            t = self.window.info_dialog.max_dark_time.value()
            r_max = min(d, 1)
            info = self.infos[channel]
            picked_locs = self.picked_locs(channel)
            n_picks = len(picked_locs)
            N = np.empty(n_picks)
            rmsd = np.empty(n_picks)
            length = np.empty(n_picks)
            dark = np.empty(n_picks)
            has_z = hasattr(picked_locs[0], 'z')
            if has_z:
                rmsd_z = np.empty(n_picks)
            new_locs = []
            progress = lib.ProgressDialog('Calculating pick statistics', 0, len(picked_locs), self)
            progress.set_value(0)
            for i, locs in enumerate(picked_locs):
                N[i] = len(locs)
                com_x = np.mean(locs.x)
                com_y = np.mean(locs.y)
                rmsd[i] = np.sqrt(np.mean((locs.x - com_x)**2 + (locs.y - com_y)**2))
                if has_z:
                    rmsd_z[i] = np.sqrt(np.mean((locs.z - np.mean(locs.z))**2))
                if not hasattr(locs, 'len'):
                    locs = postprocess.link(locs, info, r_max=r_max, max_dark_time=t)
                length[i] = estimate_kinetic_rate(locs.len)
                locs = postprocess.compute_dark_times(locs)
                dark[i] = estimate_kinetic_rate(locs.dark)
                new_locs.append(locs)
                progress.set_value(i + 1)
            self.window.info_dialog.n_localizations_mean.setText('{:.2f}'.format(np.mean(N)))
            self.window.info_dialog.n_localizations_std.setText('{:.2f}'.format(np.std(N)))
            self.window.info_dialog.rmsd_mean.setText('{:.2}'.format(np.mean(rmsd)))
            self.window.info_dialog.rmsd_std.setText('{:.2}'.format(np.std(rmsd)))
            if has_z:
                self.window.info_dialog.rmsd_z_mean.setText('{:.2f}'.format(np.mean(rmsd_z)))
                self.window.info_dialog.rmsd_z_std.setText('{:.2f}'.format(np.std(rmsd_z)))
            pooled_locs = stack_arrays(new_locs, usemask=False, asrecarray=True)
            fit_result_len = fit_cum_exp(pooled_locs.len)
            fit_result_dark = fit_cum_exp(pooled_locs.dark)
            self.window.info_dialog.length_mean.setText('{:.2f}'.format(np.nanmean(length)))
            self.window.info_dialog.length_std.setText('{:.2f}'.format(np.nanstd(length)))
            self.window.info_dialog.dark_mean.setText('{:.2f}'.format(np.nanmean(dark)))
            self.window.info_dialog.dark_std.setText('{:.2f}'.format(np.nanstd(dark)))
            self.window.info_dialog.pick_info = {'pooled dark': estimate_kinetic_rate(pooled_locs.dark),
                                                 'length': length,
                                                 'dark': dark}
            self.window.info_dialog.update_n_units()
            self.window.info_dialog.pick_hist_window.plot(pooled_locs, fit_result_len, fit_result_dark)

    def update_pick_info_short(self):
        self.window.info_dialog.n_picks.setText(str(len(self._picks)))

    def update_scene(self, viewport=None, autoscale=False, use_cache=False, picks_only=False):
        n_channels = len(self.locs)
        if n_channels:
            viewport = viewport or self.viewport
            self.draw_scene(viewport, autoscale=autoscale, use_cache=use_cache, picks_only=picks_only)
            self.update_cursor()

    def viewport_center(self, viewport=None):
        if viewport is None:
            viewport = self.viewport
        return ((viewport[1][0] + viewport[0][0]) / 2), ((viewport[1][1] + viewport[0][1]) / 2)

    def viewport_height(self, viewport=None):
        if viewport is None:
            viewport = self.viewport
        return viewport[1][0] - viewport[0][0]

    def viewport_size(self, viewport=None):
        if viewport is None:
            viewport = self.viewport
        return self.viewport_height(viewport), self.viewport_width(viewport)

    def viewport_width(self, viewport=None):
        if viewport is None:
            viewport = self.viewport
        return viewport[1][1] - viewport[0][1]

    def zoom(self, factor):
        viewport_height, viewport_width = self.viewport_size()
        new_viewport_height_half = 0.5 * viewport_height * factor
        new_viewport_width_half = 0.5 * viewport_width * factor
        viewport_center_y, viewport_center_x = self.viewport_center()
        new_viewport = [(viewport_center_y - new_viewport_height_half,
                         viewport_center_x - new_viewport_width_half),
                        (viewport_center_y + new_viewport_height_half,
                         viewport_center_x + new_viewport_width_half)]
        self.update_scene(new_viewport)

    def zoom_in(self):
        self.zoom(1 / ZOOM)

    def zoom_out(self):
        self.zoom(ZOOM)


class Window(QtGui.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Picasso: Render')
        this_directory = os.path.dirname(os.path.realpath(__file__))
        icon_path = os.path.join(this_directory, 'icons', 'render.ico')
        icon = QtGui.QIcon(icon_path)
        self.setWindowIcon(icon)
        self.view = View(self)
        self.view.setMinimumSize(1, 1)
        self.setCentralWidget(self.view)
        self.display_settings_dialog = DisplaySettingsDialog(self)
        self.tools_settings_dialog = ToolsSettingsDialog(self)
        self.info_dialog = InfoDialog(self)
        self.menu_bar = self.menuBar()
        file_menu = self.menu_bar.addMenu('File')
        open_action = file_menu.addAction('Open')
        open_action.setShortcut(QtGui.QKeySequence.Open)
        open_action.triggered.connect(self.open_file_dialog)
        save_action = file_menu.addAction('Save localizations')
        save_action.setShortcut('Ctrl+S')
        save_action.triggered.connect(self.save_locs)
        save_picked_action = file_menu.addAction('Save picked localizations')
        save_picked_action.setShortcut('Ctrl+Shift+S')
        save_picked_action.triggered.connect(self.save_picked_locs)
        save_pick_properties_action = file_menu.addAction('Save pick properties')
        save_pick_properties_action.triggered.connect(self.save_pick_properties)
        save_picks_action = file_menu.addAction('Save pick regions')
        save_picks_action.triggered.connect(self.save_picks)
        load_picks_action = file_menu.addAction('Load pick regions')
        load_picks_action.triggered.connect(self.load_picks)
        file_menu.addSeparator()
        export_current_action = file_menu.addAction('Export current view')
        export_current_action.setShortcut('Ctrl+E')
        export_current_action.triggered.connect(self.export_current)
        export_complete_action = file_menu.addAction('Export complete image')
        export_complete_action.setShortcut('Ctrl+Shift+E')
        export_complete_action.triggered.connect(self.export_complete)
        view_menu = self.menu_bar.addMenu('View')
        display_settings_action = view_menu.addAction('Display settings')
        display_settings_action.setShortcut('Ctrl+D')
        display_settings_action.triggered.connect(self.display_settings_dialog.show)
        view_menu.addAction(display_settings_action)
        view_menu.addSeparator()
        to_left_action = view_menu.addAction('Left')
        to_left_action.setShortcut('Left')
        to_left_action.triggered.connect(self.view.to_left)
        to_right_action = view_menu.addAction('Right')
        to_right_action.setShortcut('Right')
        to_right_action.triggered.connect(self.view.to_right)
        to_up_action = view_menu.addAction('Up')
        to_up_action.setShortcut('Up')
        to_up_action.triggered.connect(self.view.to_up)
        to_down_action = view_menu.addAction('Down')
        to_down_action.setShortcut('Down')
        to_down_action.triggered.connect(self.view.to_down)
        view_menu.addSeparator()
        zoom_in_action = view_menu.addAction('Zoom in')
        zoom_in_action.setShortcuts(['Ctrl++', 'Ctrl+='])
        zoom_in_action.triggered.connect(self.view.zoom_in)
        view_menu.addAction(zoom_in_action)
        zoom_out_action = view_menu.addAction('Zoom out')
        zoom_out_action.setShortcut('Ctrl+-')
        zoom_out_action.triggered.connect(self.view.zoom_out)
        view_menu.addAction(zoom_out_action)
        fit_in_view_action = view_menu.addAction('Fit image to window')
        fit_in_view_action.setShortcut('Ctrl+W')
        fit_in_view_action.triggered.connect(self.view.fit_in_view)
        view_menu.addAction(fit_in_view_action)
        view_menu.addSeparator()
        info_action = view_menu.addAction('Show info')
        info_action.setShortcut('Ctrl+I')
        info_action.triggered.connect(self.info_dialog.show)
        view_menu.addAction(info_action)
        tools_menu = self.menu_bar.addMenu('Tools')
        tools_actiongroup = QtGui.QActionGroup(self.menu_bar)
        zoom_tool_action = tools_actiongroup.addAction(QtGui.QAction('Zoom', tools_menu, checkable=True))
        zoom_tool_action.setShortcut('Ctrl+Z')
        tools_menu.addAction(zoom_tool_action)
        zoom_tool_action.setChecked(True)
        pick_tool_action = tools_actiongroup.addAction(QtGui.QAction('Pick', tools_menu, checkable=True))
        pick_tool_action.setShortcut('Ctrl+P')
        tools_menu.addAction(pick_tool_action)
        tools_actiongroup.triggered.connect(self.view.set_mode)
        tools_menu.addSeparator()
        pick_similar_action = tools_menu.addAction('Pick similar')
        pick_similar_action.setShortcut('Ctrl+Shift+P')
        pick_similar_action.triggered.connect(self.view.pick_similar)
        clear_picks_action = tools_menu.addAction('Clear picks')
        clear_picks_action.triggered.connect(self.view.clear_picks)
        tools_menu.addSeparator()
        tools_settings_action = tools_menu.addAction('Tools settings')
        tools_settings_action.setShortcut('Ctrl+T')
        tools_settings_action.triggered.connect(self.tools_settings_dialog.show)
        postprocess_menu = self.menu_bar.addMenu('Postprocess')
        undrift_action = postprocess_menu.addAction('Undrift by RCC')
        undrift_action.setShortcut('Ctrl+U')
        undrift_action.triggered.connect(self.view.undrift)
        undrift_from_picked_action = postprocess_menu.addAction('Undrift from picked (3D)')
        undrift_from_picked_action.setShortcut('Ctrl+Shift+U')
        undrift_from_picked_action.triggered.connect(self.view.undrift_from_picked)
        link_action = postprocess_menu.addAction('Link localizations (3D)')
        link_action.triggered.connect(self.view.link)
        align_action = postprocess_menu.addAction('Align channels (3D if picked)')
        align_action.triggered.connect(self.view.align)
        combine_action = postprocess_menu.addAction('Combine picked (3D)')
        combine_action.triggered.connect(self.view.combine)
        apply_action = postprocess_menu.addAction('Apply expression to localizations')
        apply_action.setShortcut('Ctrl+A')
        apply_action.triggered.connect(self.open_apply_dialog)
        self.load_user_settings()

    def closeEvent(self, event):
        settings = io.load_user_settings()
        settings['Render']['Colormap'] = self.display_settings_dialog.colormap.currentText()
        io.save_user_settings(settings)
        QtGui.qApp.closeAllWindows()

    def export_current(self):
        try:
            base, ext = os.path.splitext(self.view.locs_paths[0])
        except AttributeError:
            return
        out_path = base + '.png'
        path = QtGui.QFileDialog.getSaveFileName(self, 'Save image', out_path, filter='*.png')
        if path:
            self.view.qimage.save(path)
        self.view.setMinimumSize(1, 1)

    def export_complete(self):
        try:
            base, ext = os.path.splitext(self.view.locs_paths[0])
        except AttributeError:
            return
        out_path = base + '.png'
        path = QtGui.QFileDialog.getSaveFileName(self, 'Save image', out_path, filter='*.png')
        if path:
            movie_height, movie_width = self.view.movie_size()
            viewport = [(0, 0), (movie_height, movie_width)]
            qimage = self.view.render_scene(cache=False, viewport=viewport)
            qimage.save(path)

    def load_picks(self):
        path = QtGui.QFileDialog.getOpenFileName(self, 'Load pick regions', filter='*.yaml')
        if path:
            self.view.load_picks(path)

    def load_user_settings(self):
        settings = io.load_user_settings()
        colormap = settings['Render']['Colormap']
        if len(colormap) == 0:
            colormap = 'magma'
        for index in range(self.display_settings_dialog.colormap.count()):
            if self.display_settings_dialog.colormap.itemText(index) == colormap:
                self.display_settings_dialog.colormap.setCurrentIndex(index)
                break

    def open_apply_dialog(self):
        cmd, channel, ok = ApplyDialog.getCmd(self)
        if ok:
            vars = self.view.locs[channel].dtype.names
            exec(cmd, {k: self.view.locs[channel][k] for k in vars})
            lib.ensure_sanity(self.view.locs[channel], self.view.infos[channel])
            self.view.index_blocks[channel] = None
            self.view.update_scene()

    def open_file_dialog(self):
        path = QtGui.QFileDialog.getOpenFileName(self, 'Add localizations', filter='*.hdf5')
        if path:
            self.view.add(path)

    def resizeEvent(self, event):
        self.update_info()

    def save_pick_properties(self):
        channel = self.view.get_channel('Save localizations')
        if channel is not None:
            base, ext = os.path.splitext(self.view.locs_paths[channel])
            out_path = base + '_pickprops.hdf5'
            path = QtGui.QFileDialog.getSaveFileName(self, 'Save pick properties', out_path, filter='*.hdf5')
            if path:
                self.view.save_pick_properties(path, channel)

    def save_locs(self):
        channel = self.view.get_channel('Save localizations')
        if channel is not None:
            base, ext = os.path.splitext(self.view.locs_paths[channel])
            out_path = base + '_render.hdf5'
            path = QtGui.QFileDialog.getSaveFileName(self, 'Save localizations', out_path, filter='*.hdf5')
            if path:
                info = self.view.infos[channel] + [{'Generated by': 'Picasso Render'}]
                io.save_locs(path, self.view.locs[channel], info)

    def save_picked_locs(self):
        channel = self.view.get_channel('Save picked localizations')
        if channel is not None:
            base, ext = os.path.splitext(self.view.locs_paths[channel])
            out_path = base + '_picked.hdf5'
            path = QtGui.QFileDialog.getSaveFileName(self, 'Save picked localizations', out_path, filter='*.hdf5')
            if path:
                self.view.save_picked_locs(path, channel)

    def save_picks(self):
        base, ext = os.path.splitext(self.view.locs_paths[0])
        out_path = base + '_picks.yaml'
        path = QtGui.QFileDialog.getSaveFileName(self, 'Save pick regions', out_path, filter='*.yaml')
        if path:
            self.view.save_picks(path)

    def update_info(self):
        self.info_dialog.width_label.setText('{} pixel'.format((self.view.width())))
        self.info_dialog.height_label.setText('{} pixel'.format((self.view.height())))
        self.info_dialog.locs_label.setText('{:,}'.format(self.view.n_locs))
        try:
            self.info_dialog.fit_precision.setText('{:.3} pixel'.format(self.view.median_lp))
        except AttributeError:
            pass


def main():
    app = QtGui.QApplication(sys.argv)
    window = Window()
    window.show()

    def excepthook(type, value, tback):
        lib.cancel_dialogs()
        QtCore.QCoreApplication.instance().processEvents()
        message = ''.join(traceback.format_exception(type, value, tback))
        errorbox = QtGui.QMessageBox.critical(window, 'An error occured', message)
        errorbox.exec_()
        sys.__excepthook__(type, value, tback)
    sys.excepthook = excepthook

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
