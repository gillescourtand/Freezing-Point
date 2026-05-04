# -*- coding: utf-8 -*-
"""
Created on Tue Jan 27 12:44:35 2026

@author: courtand
"""
# -*- coding: utf-8 -*-
"""
Created on Wed Dec 17 15:50:35 2025

@author: gille
"""

import sys
import pandas as pd
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox, 
                             QFileDialog, QMessageBox, QGroupBox)
from PyQt5.QtCore import Qt, pyqtSignal


class ManualScoringPanel(QWidget):
    # Define signal to emit merged dataframe
    dataframe_updated = pyqtSignal(object)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manual_score_dataframe = None
        self.current_filepath = None
        #values from main interface
        self.app_dataframe = None 
        self.framerate = None
        self.init_ui()
    
    def set_app_dataframe(self, dataframe):
        """
        Set or update the app dataframe
        Call this method when user loads/updates data in your app
        """
        self.app_dataframe = dataframe
        print(f"App dataframe updated: {len(dataframe) if dataframe is not None else 0} rows")
    
    def set_framerate(self, framerate):
        """
        Set or update the framerate
        Call this method when user provides framerate value
        """
        self.framerate = framerate
        self.framerate_edit.setText(str(self.framerate))
        print(f"Framerate updated: {framerate} fps")
    
    def init_ui(self):
        """Initialize the user interface"""
        layout = QVBoxLayout()
        
        # Group box for manual scoring
        group_box = QGroupBox("Manual Scoring - Noldus Observer")
        group_layout = QVBoxLayout()
        
        # File selection section
        file_layout = QVBoxLayout()
        self.load_button = QPushButton("Load Scoring File")
        self.load_button.clicked.connect(self.load_scoring_file)
        
        self.description_label = QLabel(f"Use Noldus Observer xls files \ requirements: 'Intervals', 'ID'")
        self.description_label.setWordWrap(True)
        
        self.filename_label = QLabel("No file loaded")
        self.filename_label.setStyleSheet("QLabel { color: gray; font-style: italic; }")
        
                
        file_layout.addWidget(self.load_button)
        file_layout.addWidget(self.description_label)
        file_layout.addWidget(self.filename_label, stretch=1)
        group_layout.addLayout(file_layout)
        
        framerate_layout = QHBoxLayout()
        self.framerate_label= QLabel("Framerate (fps) ")
        self.framerate_label.setMaximumWidth(90)
        self.framerate_edit = QLineEdit()
        self.framerate_edit.setMaximumWidth(50)
        self.framerate_emptyLabel = QLabel("")
        framerate_layout.addWidget(self.framerate_label)
        framerate_layout.addWidget(self.framerate_edit)
        framerate_layout.addWidget(self.framerate_emptyLabel)

        group_layout.addLayout(framerate_layout)
        
        # ID selection section
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel("Select ID "))
        
        self.id_combobox = QComboBox()
        self.id_combobox.setEnabled(False)
        self.id_combobox.currentTextChanged.connect(self.on_id_selected)
        id_layout.addWidget(self.id_combobox, stretch=1)
        
        self.process_button = QPushButton("Process Selected ID")
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self.process_selected_id)
        
        id_layout.addWidget(self.process_button)
        group_layout.addLayout(id_layout)
        
        # Info label
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        
        group_layout.addWidget(self.info_label)
        group_box.setLayout(group_layout)
        layout.addWidget(group_box)
        
        self.setLayout(layout)
    
    def load_scoring_file(self):
        """Load CSV/XLS/XLSX file from Noldus Observer"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Manual Scoring File",
            "",
            "Supported Files (*.csv *.xls *.xlsx);;CSV Files (*.csv);;Excel Files (*.xls *.xlsx);;All Files (*)"
        )
        
        if not file_path:
            return
        
        try:
            # Load file based on extension
            if file_path.endswith('.csv'):
                self.manual_score_dataframe = pd.read_csv(file_path)
            elif file_path.endswith(('.xls', '.xlsx')):
                self.manual_score_dataframe = pd.read_excel(file_path)
            else:
                QMessageBox.warning(self, "Error", "Unsupported file format")
                return
            
            # Update UI
            self.current_filepath = file_path
            filename = file_path.split('/')[-1]  # Get just the filename
            self.filename_label.setText(f"📄 {filename}")
            self.filename_label.setStyleSheet("QLabel { color: green; }")
            
            # Extract unique IDs
            self.extract_unique_ids()
            
            # Show file info
            self.info_label.setText(
                f"Loaded: {len(self.manual_score_dataframe)} rows, {len(self.manual_score_dataframe.columns)} columns"
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Error Loading File", f"Failed to load file:\n{str(e)}")
            self.reset_ui()
    
    def extract_unique_ids(self):
        """Extract unique values from the 'ID' column and populate combobox"""
        if self.manual_score_dataframe is None:
            return
        
        # Check if 'ID' column exists
        if 'ID' not in self.manual_score_dataframe.columns:
            QMessageBox.warning(
                self, 
                "Missing ID Column", 
                f"The file does not contain an 'ID' column.\nAvailable columns: {', '.join(self.current_dataframe.columns)}"
            )
            self.reset_ui()
            return
        
        # Get unique IDs and sort them
        unique_ids = self.manual_score_dataframe['ID'].dropna().unique()
        unique_ids = sorted([str(id_val) for id_val in unique_ids])
        
        # Populate combobox
        self.id_combobox.clear()
        self.id_combobox.addItems(unique_ids)
        self.id_combobox.setEnabled(True)
        self.process_button.setEnabled(True)
        
        self.info_label.setText(
            f"Loaded: {len(self.manual_score_dataframe)} rows, {len(self.manual_score_dataframe.columns)} columns | "
            f"Found {len(unique_ids)} unique IDs"
        )
    
    def on_id_selected(self, selected_id):
        """Called when an ID is selected in the combobox"""
        if not selected_id or self.manual_score_dataframe is None:
            return
        
        # Count rows for this ID
        count = len(self.manual_score_dataframe[self.manual_score_dataframe['ID'] == selected_id])
        self.info_label.setText(
            f"Loaded: {len(self.manual_score_dataframe)} rows, {len(self.manual_score_dataframe.columns)} columns | "
            f"Selected ID '{selected_id}' has {count} rows"
        )
    
    def process_selected_id(self):
        """Process the selected ID - calls your custom function"""
        selected_id = self.id_combobox.currentText()
        
        if not selected_id or self.manual_score_dataframe is None:
            QMessageBox.warning(self, "No Selection", "Please select an ID first")
            return
        
        # Extract data for selected ID
        filtered_data = self.get_data_for_id(selected_id)
        #extract interval to display
        manual_intervals=self.extract_intervals(selected_id, filtered_data)
        print("manual_intervals")
        print(manual_intervals)
        manual_score_merged_df=self.merge_manual_intervals_current_result(self.app_dataframe,manual_intervals)
        # return manual_score_merged_df
        # Emit signal with merged dataframe
        if manual_score_merged_df is not None:
            print(f"DEBUG: Emitting dataframe_updated signal with {len(manual_score_merged_df)} rows")
            self.dataframe_updated.emit(manual_score_merged_df)
    
    def get_data_for_id(self, selected_id):
        """Extract all rows for a specific ID from the dataframe"""
        if self.manual_score_dataframe is None:
            return None
        # print(self.manual_score_dataframe)
        
        selected_id_num = float(selected_id)
        filtered_df = self.manual_score_dataframe[
            self.manual_score_dataframe['ID'].astype(float) == selected_id_num].copy()
        # print(filtered_df)
        return filtered_df
    
    def extract_intervals(self, id_value, data):
        """
        Replace this with your actual processing function
        
        Parameters:
        -----------
        id_value : str
            The selected ID value
        data : pandas.DataFrame
            Filtered dataframe containing only rows for the selected ID
        """
        
        framerate= float(self.framerate_edit.text())

        
        intervals_data = data[['Intervals','ID']].copy()
        print(intervals_data)
        # Split the "Intervals" column into start and stop times in seconds
        intervals_data[['Start time (s)', 'Stop time (s)']] = intervals_data['Intervals'].str.split('-', expand=True)
        
        # Convert the split times to numeric values for calculation
        intervals_data['Start time (s)'] = pd.to_numeric(intervals_data['Start time (s)'].str.replace(',', '.'), errors='coerce')
        intervals_data['Stop time (s)'] = pd.to_numeric(intervals_data['Stop time (s)'].str.replace(',', '.'), errors='coerce')
        
        intervals_data['Start frame'] = (intervals_data['Start time (s)'] * framerate).astype(int)
        intervals_data['Stop frame'] = (intervals_data['Stop time (s)'] * framerate).astype(int)
        
        # Display the resulting frame data
        intervals_data[['Start frame', 'Stop frame']].head()
        
        return intervals_data
    
    def merge_manual_intervals_current_result(self,freezing_df,manual_intervals):
        # initialize 'manual_scoring'
        freezing_df['manual_scoring'] = False
        # Extract frame tuples (start, stop) from the extracted ID manual score file
        frame_tuples = list(zip(manual_intervals['Start frame'], manual_intervals['Stop frame']))
        # Update 'manual_scoring' column in the first CSV file based on frame_tuples
        for start_frame, stop_frame in frame_tuples:
            freezing_df.loc[(freezing_df['frame_idx'] >= start_frame) & (freezing_df['frame_idx'] <= stop_frame), 'manual_scoring'] = True

        print(freezing_df)
        return freezing_df
    
    def reset_ui(self):
        """Reset UI to initial state"""
        self.manual_score_dataframe = None
        self.current_filepath = None
        self.filename_label.setText("No file loaded")
        self.filename_label.setStyleSheet("QLabel { color: gray; font-style: italic; }")
        self.id_combobox.clear()
        self.id_combobox.setEnabled(False)
        self.process_button.setEnabled(False)
        self.info_label.setText("")


# Example application to demonstrate the panel
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manual Scoring Panel - Noldus Observer")
        self.setGeometry(100, 100, 700, 300)
        
        # Initialize with no data
        self.my_app_dataframe = None
        self.my_framerate = None
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create layout
        layout = QVBoxLayout()
        
        # Add manual scoring panel (starts with None values)
        self.scoring_panel = ManualScoringPanel(parent=self)
        layout.addWidget(self.scoring_panel)
        
        # Add simulation buttons to show how to update values
        button_layout = QHBoxLayout()
        
        load_data_btn = QPushButton("Simulate: Load App Data")
        load_data_btn.clicked.connect(self.simulate_load_data)
        button_layout.addWidget(load_data_btn)
        
        set_framerate_btn = QPushButton("Simulate: Set Framerate")
        set_framerate_btn.clicked.connect(self.simulate_set_framerate)
        button_layout.addWidget(set_framerate_btn)
        
        layout.addLayout(button_layout)
        
        central_widget.setLayout(layout)
    
    def simulate_load_data(self):
        """Simulate user loading data in your app"""
        # When user loads data in your app, create/update dataframe
        self.my_app_dataframe = pd.DataFrame({
            'ID': ['Subject1', 'Subject2', 'Subject3'],
            'Value': [100, 200, 300],
            'Timestamp': [0.0, 1.0, 2.0]
        })
        
        # IMPORTANT: Update the scoring panel with new dataframe
        self.scoring_panel.set_app_dataframe(self.my_app_dataframe)
        
        QMessageBox.information(self, "Data Loaded", 
                               f"App dataframe loaded with {len(self.my_app_dataframe)} rows")
    
    def simulate_set_framerate(self):
        """Simulate user providing framerate"""
        # When user provides framerate (e.g., from input field or file)
        self.my_framerate = 30.0
        
        # IMPORTANT: Update the scoring panel with framerate
        self.scoring_panel.set_framerate(self.my_framerate)
        
        QMessageBox.information(self, "Framerate Set", 
                               f"Framerate set to {self.my_framerate} fps")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())